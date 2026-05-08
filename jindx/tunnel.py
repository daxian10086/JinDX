"""TLS 证书管理和 HTTP CONNECT 隧道。"""

import asyncio
import base64
import hashlib
import logging
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import WebSocketDisconnect

from .config import config, CERT_DIR, CERT_FILE, KEY_FILE, PROXY_PORT, CONNECT_PORT, TLS_PORT

logger = logging.getLogger(__name__)


# ── TLS 证书管理 ──────────────────────────────────────────────────

def _generate_cert_cryptography():
    """使用 cryptography 库生成自签名证书（跨平台，无需 openssl）。"""
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])

    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.DNSName("api.openai.com"),
            ]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=False)
        .sign(key, hashes.SHA256())
    )

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    KEY_FILE.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    CERT_FILE.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def _generate_cert_openssl():
    """使用 openssl CLI 生成自签名证书（回退选项）。"""
    from subprocess import run
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    run([
        "openssl", "req", "-x509", "-newkey", "rsa:2048",
        "-keyout", str(KEY_FILE), "-out", str(CERT_FILE),
        "-days", "3650", "-nodes",
        "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,DNS:api.openai.com",
        "-addext", "basicConstraints=CA:FALSE",
        "-addext", "keyUsage=digitalSignature,keyEncipherment",
        "-addext", "extendedKeyUsage=serverAuth",
    ], check=True, capture_output=True)


def ensure_certs():
    """生成自签名证书用于 TLS 终止。优先使用 cryptography 库，回退到 openssl CLI。"""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return

    # 优先尝试 cryptography（跨平台）
    try:
        _generate_cert_cryptography()
        logger.info(f"Generated self-signed TLS cert (cryptography): {CERT_FILE}")
        return
    except ImportError:
        logger.debug("cryptography not available, falling back to openssl")
    except Exception as e:
        logger.debug(f"cryptography cert generation failed: {e}, falling back to openssl")

    # 回退到 openssl CLI
    try:
        _generate_cert_openssl()
        logger.info(f"Generated self-signed TLS cert (openssl): {CERT_FILE}")
    except FileNotFoundError:
        logger.warning(
            "openssl not found. Install openssl or 'pip install cryptography' to enable TLS. "
            "The proxy will still work for HTTP/WS on port %d.", PROXY_PORT
        )
    except Exception as e:
        logger.warning("Certificate generation failed: %s. TLS may not work.", e)


# ── WebSocket 隧道适配器 ─────────────────────────────────────────

class TunnelWsAdapter:
    """使原始 asyncio stream pair 的行为类似 FastAPI WebSocket。"""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self._reader = reader
        self._writer = writer
        self._buf = b""

    async def accept(self):
        """通过原始流执行 WebSocket 服务器握手。"""
        request_data = await asyncio.wait_for(
            self._reader.readuntil(b'\r\n\r\n'), timeout=10
        )
        request_text = request_data.decode()
        lines = request_text.split('\r\n')
        if not lines or not lines[0].startswith('GET '):
            raise Exception(f"Expected WebSocket upgrade, got: {lines[0] if lines else 'empty'}")

        ws_key = None
        for line in lines[1:]:
            if line.lower().startswith('sec-websocket-key:'):
                ws_key = line.split(':', 1)[1].strip()
                break
        if not ws_key:
            raise Exception("No Sec-WebSocket-Key in upgrade request")

        GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        digest = hashlib.sha1((ws_key + GUID).encode()).digest()
        accept = base64.b64encode(digest).decode()

        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n"
            "\r\n"
        )
        self._writer.write(response.encode())
        await self._writer.drain()

    async def receive_text(self) -> str:
        while True:
            if len(self._buf) < 2:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise WebSocketDisconnect()
                self._buf += chunk
                continue

            b0 = self._buf[0]
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise WebSocketDisconnect()
            if opcode == 0x9:
                b1 = self._buf[1]
                length = b1 & 0x7F
                header_len = 2
                if length == 126:
                    header_len = 4
                elif length == 127:
                    header_len = 10
                mask_flag = (b1 & 0x80) != 0
                if mask_flag:
                    header_len += 4
                if len(self._buf) < header_len + length:
                    chunk = await self._reader.read(4096)
                    self._buf += chunk
                    continue
                pong = bytearray([0x8A, b1 & 0x7F])
                pong += self._buf[2:2 + length]
                self._writer.write(bytes(pong))
                await self._writer.drain()
                self._buf = self._buf[header_len + length:]
                continue

            if opcode not in (0x1, 0x2):
                raise Exception(f"Unexpected WebSocket opcode: {opcode}")

            b1 = self._buf[1]
            masked = (b1 & 0x80) != 0
            length = b1 & 0x7F

            pos = 2
            if length == 126:
                if len(self._buf) < 4:
                    chunk = await self._reader.read(4096)
                    self._buf += chunk
                    continue
                length = int.from_bytes(self._buf[2:4], 'big')
                pos = 4
            elif length == 127:
                if len(self._buf) < 10:
                    chunk = await self._reader.read(4096)
                    self._buf += chunk
                    continue
                length = int.from_bytes(self._buf[2:10], 'big')
                pos = 10

            mask_key = b""
            if masked:
                if len(self._buf) < pos + 4:
                    chunk = await self._reader.read(4096)
                    self._buf += chunk
                    continue
                mask_key = self._buf[pos:pos + 4]
                pos += 4

            total_needed = pos + length
            if len(self._buf) < total_needed:
                chunk = await self._reader.read(4096)
                if not chunk:
                    raise WebSocketDisconnect()
                self._buf += chunk
                continue

            payload = bytearray(self._buf[pos:pos + length])
            if masked:
                for i in range(length):
                    payload[i] ^= mask_key[i % 4]

            self._buf = self._buf[total_needed:]
            return bytes(payload).decode('utf-8')

    async def send_json(self, data: dict):
        import json
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        frame = self._build_ws_frame(payload, opcode=0x1)
        self._writer.write(frame)
        await self._writer.drain()

    @staticmethod
    def _build_ws_frame(payload: bytes, opcode: int = 0x1) -> bytes:
        length = len(payload)
        header = bytearray()
        header.append(0x80 | opcode)
        if length < 126:
            header.append(length)
        elif length < 65536:
            header.append(126)
            header.extend(length.to_bytes(2, 'big'))
        else:
            header.append(127)
            header.extend(length.to_bytes(8, 'big'))
        return bytes(header) + payload

    async def close(self):
        try:
            frame = self._build_ws_frame(b"", opcode=0x8)
            self._writer.write(frame)
            await self._writer.drain()
        except (ConnectionError, OSError):
            pass
        try:
            self._writer.close()
        except (ConnectionError, OSError):
            pass


# ── CONNECT 隧道服务器 ───────────────────────────────────────────

async def _run_connect_server():
    """启动原始 TCP 服务器，处理 HTTP CONNECT + TLS 终止，
    然后透明代理到本地 HTTP/WS 服务器。"""
    if CONNECT_PORT == 0:
        logger.info("CONNECT tunnel DISABLED (CONNECT_PORT=0)")
        return

    ensure_certs()

    async def pipe(src_reader: asyncio.StreamReader, dst_writer: asyncio.StreamWriter, label: str):
        try:
            while True:
                data = await src_reader.read(65536)
                if not data:
                    break
                dst_writer.write(data)
                await dst_writer.drain()
        except (ConnectionError, asyncio.TimeoutError, asyncio.IncompleteReadError):
            pass
        except Exception as e:
            logger.debug(f"Pipe {label} error: {e}")
        finally:
            try:
                dst_writer.close()
            except (ConnectionError, OSError):
                pass

    async def handle_connect(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info('peername')
        try:
            data = await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'), timeout=10)
            first_line = data.split(b'\r\n')[0].decode()

            if not first_line.startswith('CONNECT '):
                writer.write(b'HTTP/1.1 405 Method Not Allowed\r\n\r\n')
                await writer.drain()
                writer.close()
                return

            writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
            await writer.drain()

            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(str(CERT_FILE), str(KEY_FILE))

            loop = asyncio.get_event_loop()
            transport = writer.transport

            tls_reader = asyncio.StreamReader()
            protocol = asyncio.StreamReaderProtocol(tls_reader)

            tls_transport = await loop.start_tls(
                transport=transport,
                protocol=protocol,
                sslcontext=ctx,
                server_side=True,
                ssl_handshake_timeout=10,
            )
            tls_writer = asyncio.StreamWriter(tls_transport, protocol, tls_reader, loop)

            backend_reader, backend_writer = await asyncio.open_connection('127.0.0.1', PROXY_PORT)

            await asyncio.gather(
                pipe(tls_reader, backend_writer, "client->backend"),
                pipe(backend_reader, tls_writer, "backend->client"),
            )

        except (ConnectionError, asyncio.TimeoutError):
            pass
        except ssl.SSLError as e:
            logger.warning(f"TLS handshake failed from {peer}: {e}")
        except Exception as e:
            logger.error(f"CONNECT tunnel error from {peer}: {e}")
        finally:
            try:
                writer.close()
            except (ConnectionError, OSError):
                pass

    server = await asyncio.start_server(handle_connect, '127.0.0.1', CONNECT_PORT)
    logger.info(f"CONNECT+TLS tunnel server listening on 127.0.0.1:{CONNECT_PORT}")

    async with server:
        await server.serve_forever()
