// Wails bridge type declarations
declare global {
  interface Window {
    go: {
      main: {
        App: {
          // proxy control
          StartProxy(): Promise<string>
          StopProxy(): Promise<string>
          RestartProxy(): Promise<string>
          GetProxyStatus(): Promise<string>

          // config
          GetConfig(): Promise<Record<string, any>>
          SaveConfig(cfg: Record<string, any>): Promise<void>
          GetAllConfig(): Promise<Record<string, any>>

          // statistics
          GetStats(): Promise<Record<string, any>>
          GetLogs(limit: number): Promise<Array<Record<string, any>>>
          GetSystemStatus(): Promise<Record<string, any>>
          GetEnvVars(mode: "codex" | "claude"): Promise<string>

          // proxy switches
          GetProxySwitchStatus(): Promise<Record<string, boolean>>
          ToggleProxySwitch(which: string, enabled: boolean): Promise<void>

          // system
          IsAdmin(): Promise<boolean>
          GetAutoStart(): Promise<boolean>
          ToggleAutoStart(enabled: boolean): Promise<void>

          // cache
          GetCacheInfo(): Promise<Record<string, any>>
          ClearCache(source: string): Promise<Record<string, any>>
        }
      }
    }
  }
}

export {}
