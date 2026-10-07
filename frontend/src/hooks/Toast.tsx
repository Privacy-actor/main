import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from 'react'
import { CircleAlert, CircleCheck, Info, X } from 'lucide-react'

type ToastKind = 'info' | 'success' | 'error'
interface ToastItem { id: number; message: string; kind: ToastKind; action?: { label: string; onClick: () => void } }
type Notify = (message: string, kind?: ToastKind, action?: ToastItem['action']) => void

const ToastContext = createContext<Notify>(() => undefined)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const nextId = useRef(1)

  const dismiss = useCallback((id: number) => setItems(current => current.filter(item => item.id !== id)), [])

  const notify = useCallback<Notify>((message, kind = 'info', action) => {
    const id = nextId.current++
    setItems(current => [...current.slice(-3), { id, message, kind, action }])
    window.setTimeout(() => dismiss(id), kind === 'error' ? 6000 : action ? 6000 : 3200)
  }, [dismiss])

  return <ToastContext.Provider value={notify}>
    {children}
    <div className="toast-stack" aria-live="polite" role="status">
      {items.map(item => {
        const Icon = item.kind === 'error' ? CircleAlert : item.kind === 'success' ? CircleCheck : Info
        return <div key={item.id} className={`toast toast-${item.kind}`}>
          <Icon size={16} aria-hidden="true"/>
          <span>{item.message}</span>
          {item.action && <button type="button" className="toast-action" onClick={() => { item.action?.onClick(); dismiss(item.id) }}>{item.action.label}</button>}
          <button type="button" className="toast-close" aria-label="关闭提示" onClick={() => dismiss(item.id)}><X size={14}/></button>
        </div>
      })}
    </div>
  </ToastContext.Provider>
}

export function useToast() {
  return useContext(ToastContext)
}
