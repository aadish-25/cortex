import CortexLogo from './CortexLogo'

export default function Brand({ small = false }: { small?: boolean }) {
  return (
    <div className={`brand ${small ? 'brand-small' : ''}`}>
      <CortexLogo size={small ? 20 : 27} />
      <div className="brand-word">cortex<span>®</span></div>
    </div>
  )
}
