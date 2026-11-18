import React from 'react'

interface CortexLogoProps {
  size?: number
  className?: string
}

export default function CortexLogo({ size = 27, className = '' }: CortexLogoProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      style={{ display: 'inline-block', flexShrink: 0, verticalAlign: 'middle' }}
      aria-label="Cortex Logo"
    >
      <rect width="32" height="32" rx="9" fill="url(#cortexLogoGrad)" />
      <path
        d="M11 16C11 13.24 13.24 11 16 11C18.76 11 21 13.24 21 16C21 18.76 18.76 21 16 21"
        stroke="#FFFFFF"
        strokeWidth="2.2"
        strokeLinecap="round"
      />
      <circle cx="16" cy="11" r="2" fill="#93C5FD" />
      <circle cx="21" cy="16" r="2" fill="#93C5FD" />
      <circle cx="16" cy="21" r="2" fill="#93C5FD" />
      <circle cx="11" cy="16" r="2.2" fill="#FFFFFF" />
      <defs>
        <linearGradient id="cortexLogoGrad" x1="0" y1="0" x2="32" y2="32" gradientUnits="userSpaceOnUse">
          <stop stopColor="#1D4ED8" />
          <stop offset="1" stopColor="#3B82F6" />
        </linearGradient>
      </defs>
    </svg>
  )
}
