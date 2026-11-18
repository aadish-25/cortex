export function Spinner({ full = false }: { full?: boolean }) {
  return <div className={full ? 'spinner-screen' : 'spinner-inline'}><span className="spinner" /></div>
}
