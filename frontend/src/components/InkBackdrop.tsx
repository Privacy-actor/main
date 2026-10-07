/** 页面底部的水墨远山。只在留白处隐约可见，纸面（白色工作区）会把它盖住，不影响阅读。 */
export default function InkBackdrop() {
  return <svg className="ink-backdrop" viewBox="0 0 1600 300" preserveAspectRatio="xMidYMax slice" aria-hidden="true" focusable="false">
    <defs>
      <filter id="ink-wash" x="-5%" y="-30%" width="110%" height="160%">
        <feTurbulence type="fractalNoise" baseFrequency="0.008 0.05" numOctaves="3" seed="11" result="noise"/>
        <feDisplacementMap in="SourceGraphic" in2="noise" scale="18" xChannelSelector="R" yChannelSelector="G" result="bled"/>
        <feGaussianBlur in="bled" stdDeviation="1.4"/>
      </filter>
      <linearGradient id="ink-far" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="var(--ink-wash-far)" stopOpacity=".55"/>
        <stop offset=".55" stopColor="var(--ink-wash-far)" stopOpacity=".12"/>
        <stop offset="1" stopColor="var(--ink-wash-far)" stopOpacity="0"/>
      </linearGradient>
      <linearGradient id="ink-mid" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="var(--ink-wash-mid)" stopOpacity=".7"/>
        <stop offset=".6" stopColor="var(--ink-wash-mid)" stopOpacity=".14"/>
        <stop offset="1" stopColor="var(--ink-wash-mid)" stopOpacity="0"/>
      </linearGradient>
      <linearGradient id="ink-near" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stopColor="var(--ink-wash-near)" stopOpacity=".85"/>
        <stop offset=".7" stopColor="var(--ink-wash-near)" stopOpacity=".2"/>
        <stop offset="1" stopColor="var(--ink-wash-near)" stopOpacity="0"/>
      </linearGradient>
    </defs>
    <g filter="url(#ink-wash)">
      {/* 远山：起伏平缓，颜色最淡 */}
      <path fill="url(#ink-far)" d="M0 196 C 70 182, 120 160, 190 150 C 250 141, 290 168, 350 160 C 420 150, 470 112, 540 104 C 600 97, 640 132, 700 138 C 770 145, 820 120, 880 116 C 950 111, 1000 148, 1070 150 C 1140 152, 1190 118, 1260 98 C 1320 82, 1370 112, 1430 128 C 1490 144, 1550 150, 1600 146 L 1600 300 L 0 300 Z"/>
      {/* 中景：右侧两座较高的峰 */}
      <path fill="url(#ink-mid)" d="M0 238 C 80 226, 140 206, 210 204 C 280 202, 330 226, 400 222 C 470 218, 520 196, 590 194 C 660 192, 700 214, 780 212 C 850 210, 900 180, 960 162 C 1010 147, 1050 168, 1100 182 C 1150 196, 1180 170, 1230 140 C 1270 116, 1310 128, 1350 156 C 1400 190, 1460 206, 1530 206 C 1560 206, 1580 210, 1600 214 L 1600 300 L 0 300 Z"/>
      {/* 近景：左下的坡与右下的矮丘 */}
      <path fill="url(#ink-near)" d="M0 262 C 60 250, 110 236, 170 240 C 230 244, 270 262, 340 266 C 420 270, 520 276, 640 278 C 760 280, 900 276, 1040 270 C 1140 266, 1220 252, 1300 244 C 1370 238, 1430 248, 1500 256 C 1540 260, 1570 262, 1600 262 L 1600 300 L 0 300 Z"/>
    </g>
  </svg>
}
