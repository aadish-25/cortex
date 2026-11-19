import CortexLogo from './CortexLogo'

export function Spinner({
  full = false,
  label = 'Connecting to workspace…',
}: {
  full?: boolean
  label?: string
}) {
  if (!full) {
    return (
      <div className="spinner-inline">
        <span className="spinner" />
      </div>
    )
  }

  return (
    <div className="spinner-screen" role="status" aria-live="polite">
      <div className="spinner-screen-inner">
        <div className="spinner-screen-brand">
          <CortexLogo size={34} />
          <span className="spinner-screen-logo-text">cortex</span>
        </div>
        <div className="spinner-progress-track">
          <div className="spinner-progress-bar" />
        </div>
        <p className="spinner-screen-label">{label}</p>
      </div>
    </div>
  )
}
