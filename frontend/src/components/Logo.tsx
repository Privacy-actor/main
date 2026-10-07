import { BRAND } from '../brand'

/** 品牌标记：一方墨色印章，白文“隐”字。边缘用轻微的噪声位移模拟钤印的毛边。 */
export function LogoMark({ size = 32 }: { size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" className="logo-mark">
    <defs>
      <filter id="seal-edge" x="-10%" y="-10%" width="120%" height="120%">
        <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed="3" result="grain"/>
        <feDisplacementMap in="SourceGraphic" in2="grain" scale="1.1" xChannelSelector="R" yChannelSelector="G"/>
      </filter>
    </defs>
    <g filter="url(#seal-edge)">
      <rect x="1.5" y="1.5" width="29" height="29" rx="4" fill="var(--ink)"/>
      <rect x="4" y="4" width="24" height="24" rx="2" fill="none" stroke="#fff" strokeOpacity=".28" strokeWidth=".8"/>
    </g>
    <text x="16" y="22.6" textAnchor="middle" fill="#fff" fontFamily="var(--font-display)" fontWeight="700" fontSize="17.5">{BRAND.seal}</text>
  </svg>
}
