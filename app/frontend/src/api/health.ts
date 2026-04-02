export interface HealthPayload {
  status: 'ok'
  service: string
  appName: string
  databasePath: string
  chromiumReady: boolean
  timestamp: string
}

interface HealthResponseDto {
  status: 'ok'
  service: string
  app_name: string
  database_path: string
  chromium_ready: boolean
  timestamp: string
}

export async function fetchHealth(): Promise<HealthPayload> {
  const response = await fetch('/api/health', {
    headers: {
      Accept: 'application/json',
    },
  })

  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`)
  }

  const payload = (await response.json()) as HealthResponseDto

  return {
    status: payload.status,
    service: payload.service,
    appName: payload.app_name,
    databasePath: payload.database_path,
    chromiumReady: payload.chromium_ready,
    timestamp: payload.timestamp,
  }
}
