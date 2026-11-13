import { useEffect, useRef, useState } from 'react'
import { api } from '../api'

// A search box that keeps a list: type, pick a suggestion (or press Enter for your own words).
// With `weights`, each item has a share you can change: Normal -> More (double) -> Most (triple).

const SHARE = { 1: 'Normal', 2: 'More', 3: 'Most' }

export default function SuggestTags({ kind, value, onChange, placeholder, weights, onWeight, testId }) {
  const [text, setText] = useState('')
  const [items, setItems] = useState([])
  const [open, setOpen] = useState(false)
  const [hi, setHi] = useState(0)
  const box = useRef(null)

  useEffect(() => {
    if (!open || !kind) return
    let stop = false
    const t = setTimeout(async () => {
      try {
        const r = await api.get(`/api/suggest/${kind}?q=${encodeURIComponent(text)}`)
        if (!stop) { setItems(r.filter((x) => !value.includes(x.value))); setHi(0) }
      } catch { if (!stop) setItems([]) }
    }, 120)
    return () => { stop = true; clearTimeout(t) }
  }, [text, open, kind, value])

  useEffect(() => {
    const close = (e) => { if (box.current && !box.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const add = (v) => {
    const t = (v ?? text).trim()
    if (t && !value.some((x) => x.toLowerCase() === t.toLowerCase())) onChange([...value, t])
    setText('')
    setOpen(false)                 // the list closes after a pick, so it never covers the next box; typing opens it
  }
  const key = (e) => {
    if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setHi((h) => Math.min(h + 1, items.length - 1)) }
    else if (e.key === 'ArrowUp') { e.preventDefault(); setHi((h) => Math.max(h - 1, 0)) }
    else if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      add(open && items[hi] && text.trim() ? items[hi].value : undefined)
    } else if (e.key === 'Escape') setOpen(false)
    else if (e.key === 'Backspace' && !text && value.length) onChange(value.slice(0, -1))
  }
  const cycle = (v) => onWeight(v, ((weights?.[v] || 1) % 3) + 1)

  return (
    <div ref={box} className="relative" data-testid={testId}>
      <div className="flex flex-wrap items-center gap-1.5 rounded-xl border border-slate-200 bg-slate-50 p-1.5 focus-within:border-teal"
        onClick={() => box.current?.querySelector('input')?.focus()}>
        {value.map((v) => {
          const w = weights?.[v] || 1
          return (
            <span key={v} className="chip border border-teal/40 bg-teal-50 text-teal-600">
              {v}
              {onWeight && <button type="button" title="Share of the searches: click to change (Normal, More = double, Most = triple)"
                onClick={(e) => { e.stopPropagation(); cycle(v) }}
                className={`ml-1.5 rounded px-1 text-[10px] font-semibold ${w > 1 ? 'bg-teal text-white' : 'bg-slate-200 text-slate-600'}`}>{SHARE[w]}</button>}
              <button type="button" aria-label={`remove ${v}`} className="ml-1 text-slate-400 hover:text-slate-700"
                onClick={(e) => { e.stopPropagation(); onChange(value.filter((x) => x !== v)) }}>×</button>
            </span>
          )
        })}
        <input className="min-w-[160px] flex-1 bg-transparent px-1 py-0.5 text-sm text-slate-900 outline-none placeholder:text-slate-400"
          value={text} placeholder={value.length ? '' : placeholder} onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
          onChange={(e) => { setText(e.target.value); setOpen(true) }} onKeyDown={key} aria-label={placeholder} />
      </div>
      {open && (items.length > 0 || text.trim()) && (
        <div className="absolute left-0 right-0 z-30 mt-1 max-h-72 overflow-y-auto rounded-xl border border-slate-200 bg-surface2 p-1 shadow-2xl backdrop-blur-xl" role="listbox">
          {items.map((x, i) => (
            <button type="button" key={x.value} role="option" aria-selected={i === hi}
              onMouseDown={(e) => { e.preventDefault(); add(x.value) }} onMouseEnter={() => setHi(i)}
              className={`flex w-full items-baseline justify-between gap-3 rounded-lg px-2.5 py-1.5 text-left text-sm ${i === hi ? 'bg-teal-50 text-teal-600' : 'text-slate-800'}`}>
              <span>{x.label}{x.hint ? <span className="ml-2 text-xs muted">also {x.hint}</span> : null}</span>
              {x.group && <span className="shrink-0 text-xs muted">{x.group}</span>}
            </button>
          ))}
          {text.trim() && !items.some((x) => x.value.toLowerCase() === text.trim().toLowerCase()) && (
            <button type="button" onMouseDown={(e) => { e.preventDefault(); add() }}
              className="w-full rounded-lg px-2.5 py-1.5 text-left text-sm muted hover:bg-slate-100">Add “{text.trim()}” as you typed it</button>
          )}
        </div>
      )}
    </div>
  )
}
