// Wails 桥接类型声明
declare global {
  interface Window {
    go: {
      main: {
        App: {
          // 代理控制
          StartProxy(): Promise<string>
          StopProxy(): Promise<string>
          RestartProxy(): Promise<string>
          GetProxyStatus(): Promise<string>

          // 配置
          GetConfig(): Promise<Record<string, any>>
          SaveConfig(cfg: Record<string, any>): Promise<void>

          // 统计
          GetStats(): Promise<Record<string, any>>
          GetLogs(limit: number): Promise<Array<Record<string, any>>>
          GetSystemStatus(): Promise<Record<string, any>>
          GetEnvVars(mode: "codex" | "claude"): Promise<string>

          // 代理开关
          GetProxySwitchStatus(): Promise<Record<string, boolean>>
          ToggleProxySwitch(which: string, enabled: boolean): Promise<void>

          // 系统
          IsAdmin(): Promise<boolean>
          GetAutoStart(): Promise<boolean>
          ToggleAutoStart(enabled: boolean): Promise<void>
        }
      }
    }
  }
}

export {}
