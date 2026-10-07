import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ChevronDown, LoaderCircle, X } from 'lucide-react'

export function Spinner({ size = 16 }: { size?: number }) {
  return <LoaderCircle className="spin" size={size} aria-hidden="true"/>
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="kbd">{children}</kbd>
}

interface SegmentedOption<T extends string | number> { value: T; label: ReactNode; title?: string; disabled?: boolean }

export function Segmented<T extends string | number>({ value, options, onChange, label, size = 'md', disabled }: {
  value: T; options: SegmentedOption<T>[]; onChange: (value: T) => void; label: string; size?: 'sm' | 'md'; disabled?: boolean
}) {
  return <div className={`segmented segmented-${size}`} role="radiogroup" aria-label={label}>
    {options.map(option => <button
      type="button" role="radio" key={String(option.value)} aria-checked={option.value === value}
      className={option.value === value ? 'is-active' : ''} title={option.title}
      disabled={disabled || option.disabled} onClick={() => option.value !== value && onChange(option.value)}
    >{option.label}</button>)}
  </div>
}

export function Toggle({ checked, onChange, label, description, disabled }: {
  checked: boolean; onChange: (checked: boolean) => void; label: ReactNode; description?: ReactNode; disabled?: boolean
}) {
  const id = useId()
  return <label className={`toggle${disabled ? ' is-disabled' : ''}`} htmlFor={id}>
    <span className="toggle-copy"><span className="toggle-label">{label}</span>{description && <span className="toggle-desc">{description}</span>}</span>
    <input id={id} type="checkbox" role="switch" checked={checked} disabled={disabled} onChange={event => onChange(event.target.checked)}/>
    <span className="toggle-track" aria-hidden="true"><span className="toggle-thumb"/></span>
  </label>
}

/** 输入后按回车或逗号生成标签。 */
export function TagInput({ values, onChange, placeholder, label }: { values: string[]; onChange: (values: string[]) => void; placeholder?: string; label: string }) {
  const [draft, setDraft] = useState('')
  function commit(raw = draft) {
    const parts = raw.split(/[,，、\n]/).map(item => item.trim()).filter(Boolean)
    if (parts.length) onChange([...new Set([...values, ...parts])])
    setDraft('')
  }
  function onKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',' || event.key === '，') {
      event.preventDefault()
      commit()
    } else if (event.key === 'Backspace' && !draft && values.length) {
      onChange(values.slice(0, -1))
    }
  }
  return <div className="tag-input" onClick={event => (event.currentTarget.querySelector('input') as HTMLInputElement | null)?.focus()}>
    {values.map(value => <span className="tag" key={value}>{value}<button type="button" aria-label={`移除 ${value}`} onClick={() => onChange(values.filter(item => item !== value))}><X size={12}/></button></span>)}
    <input aria-label={label} value={draft} placeholder={values.length ? '' : placeholder} onChange={event => setDraft(event.target.value)} onKeyDown={onKeyDown} onBlur={() => commit()}/>
  </div>
}

export function Dialog({ open, onClose, title, description, children, footer, size = 'md' }: {
  open: boolean; onClose: () => void; title: ReactNode; description?: ReactNode; children: ReactNode; footer?: ReactNode; size?: 'sm' | 'md' | 'lg' | 'xl'
}) {
  const panel = useRef<HTMLDivElement>(null)
  // onClose 多是每次渲染新建的箭头函数。放进 ref 里，打开期间父组件重新渲染（引擎状态轮询、提示条）
  // 不会重跑下面的效果，否则焦点会被拉回第一个输入框，正在输入的光标跳到别处
  const closeRef = useRef(onClose)
  useEffect(() => { closeRef.current = onClose })
  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    const focusTarget = panel.current?.querySelector<HTMLElement>('[data-autofocus], input, textarea, select, button:not(.dialog-close)')
    focusTarget?.focus()
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') closeRef.current() }
    window.addEventListener('keydown', onKey)
    document.body.classList.add('has-dialog')
    return () => { window.removeEventListener('keydown', onKey); document.body.classList.remove('has-dialog'); previous?.focus?.() }
  }, [open])
  if (!open) return null
  return <div className="dialog-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className={`dialog dialog-${size}`} role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined} ref={panel}>
      <header className="dialog-head">
        <div><h2>{title}</h2>{description && <p>{description}</p>}</div>
        <button type="button" className="icon-btn dialog-close" aria-label="关闭" onClick={onClose}><X size={18}/></button>
      </header>
      <div className="dialog-body">{children}</div>
      {footer && <footer className="dialog-foot">{footer}</footer>}
    </div>
  </div>
}

export interface MenuItem { label: ReactNode; description?: ReactNode; icon?: ReactNode; onSelect: () => void; disabled?: boolean; tone?: 'danger' }

export function MenuButton({ label, icon, items, align = 'end', className = 'btn' }: { label: ReactNode; icon?: ReactNode; items: MenuItem[]; align?: 'start' | 'end'; className?: string }) {
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDown = (event: MouseEvent) => { if (!root.current?.contains(event.target as Node)) setOpen(false) }
    const onKey = (event: globalThis.KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('mousedown', onDown)
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('mousedown', onDown); window.removeEventListener('keydown', onKey) }
  }, [open])
  return <div className="menu" ref={root}>
    <button type="button" className={className} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen(value => !value)}>{icon}{label}<ChevronDown size={14} className="menu-caret"/></button>
    {open && <div className={`menu-pop menu-${align}`} role="menu">
      {items.map((item, index) => <button type="button" role="menuitem" key={index} disabled={item.disabled} className={item.tone === 'danger' ? 'is-danger' : ''} onClick={() => { setOpen(false); item.onSelect() }}>
        {item.icon && <span className="menu-icon">{item.icon}</span>}
        <span className="menu-copy"><span>{item.label}</span>{item.description && <small>{item.description}</small>}</span>
      </button>)}
    </div>}
  </div>
}

export function EmptyState({ title, children, action, illustration = 'document' }: { title: ReactNode; children?: ReactNode; action?: ReactNode; illustration?: 'document' | 'queue' | 'folder' }) {
  return <div className="empty">
    <RedactionArt variant={illustration}/>
    <h3>{title}</h3>
    {children && <p>{children}</p>}
    {action && <div className="empty-action">{action}</div>}
  </div>
}

/** 空状态插图：几行文字，其中一行被墨条遮住。 */
export function RedactionArt({ variant = 'document' }: { variant?: 'document' | 'queue' | 'folder' }) {
  if (variant === 'queue') return <svg className="empty-art" width="96" height="64" viewBox="0 0 96 64" aria-hidden="true">
    <rect x="8" y="6" width="80" height="14" rx="3" fill="var(--sheet)" stroke="var(--rule)"/>
    <rect x="16" y="11.5" width="34" height="3" rx="1.5" fill="var(--ink-4)"/>
    <rect x="8" y="25" width="80" height="14" rx="3" fill="var(--sheet)" stroke="var(--rule)"/>
    <rect x="16" y="30.5" width="22" height="3" rx="1.5" fill="var(--ink-4)"/><rect x="42" y="29" width="20" height="6" rx="1.5" fill="var(--ink)"/>
    <rect x="8" y="44" width="80" height="14" rx="3" fill="var(--sheet)" stroke="var(--rule)"/>
    <rect x="16" y="49.5" width="40" height="3" rx="1.5" fill="var(--ink-4)"/>
  </svg>
  return <svg className="empty-art" width="96" height="72" viewBox="0 0 96 72" aria-hidden="true">
    <rect x="18" y="4" width="60" height="64" rx="5" fill="var(--sheet)" stroke="var(--rule)"/>
    <rect x="27" y="15" width="40" height="3" rx="1.5" fill="var(--ink-4)"/>
    <rect x="27" y="24" width="14" height="3" rx="1.5" fill="var(--ink-4)"/><rect x="44" y="22.5" width="23" height="6" rx="1.5" fill="var(--ink)"/>
    <rect x="27" y="33" width="36" height="3" rx="1.5" fill="var(--ink-4)"/>
    <rect x="27" y="42" width="24" height="3" rx="1.5" fill="var(--ink-4)"/>
    <rect x="27" y="51" width="30" height="3" rx="1.5" fill="var(--ink-4)"/>
    {variant === 'folder' && <rect x="58" y="44" width="22" height="18" rx="3" fill="var(--cobalt)" opacity=".9"/>}
  </svg>
}

/** 处理中：一组被逐行遮住的文字线条。 */
export function RedactionSkeleton({ lines = 7 }: { lines?: number }) {
  const widths = [92, 78, 86, 64, 90, 72, 55, 84, 68]
  return <div className="redaction-skeleton" aria-hidden="true">
    {Array.from({ length: lines }, (_, index) => <div className="rs-line" key={index} style={{ width: `${widths[index % widths.length]}%`, animationDelay: `${index * 90}ms` }}>
      <span className="rs-bar" style={{ left: `${(index * 23) % 55}%`, width: `${18 + (index * 7) % 20}%`, animationDelay: `${index * 140}ms` }}/>
    </div>)}
  </div>
}

export function Field({ label, hint, children, htmlFor }: { label: ReactNode; hint?: ReactNode; children: ReactNode; htmlFor?: string }) {
  return <div className="field">
    <label className="field-label" htmlFor={htmlFor}>{label}</label>
    {children}
    {hint && <p className="field-hint">{hint}</p>}
  </div>
}

/** 需要用户确认的操作（删除、清理等）。 */
export function ConfirmDialog({ open, title, description, confirmLabel, tone = 'danger', busy, onConfirm, onClose, children }: {
  open: boolean; title: ReactNode; description?: ReactNode; confirmLabel: string; tone?: 'danger' | 'primary'; busy?: boolean
  onConfirm: () => void; onClose: () => void; children?: ReactNode
}) {
  return <Dialog open={open} onClose={onClose} size="sm" title={title} description={description} footer={<>
    <button type="button" className="btn" onClick={onClose} disabled={busy}>取消</button>
    <button type="button" className={`btn ${tone === 'danger' ? 'btn-danger-solid' : 'btn-primary'}`} onClick={onConfirm} disabled={busy} data-autofocus>{busy && <Spinner size={14}/>}{confirmLabel}</button>
  </>}>{children}</Dialog>
}

/** 进度条。 */
export function Progress({ value, tone = 'cobalt', label }: { value: number; tone?: 'cobalt' | 'ok' | 'warn' | 'bad'; label?: string }) {
  const percent = Math.max(0, Math.min(100, value))
  return <div className={`progress is-${tone}`} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(percent)} aria-label={label}>
    <span style={{ width: `${percent}%` }}/>
  </div>
}
