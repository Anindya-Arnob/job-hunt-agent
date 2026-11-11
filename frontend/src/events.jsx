import { createContext, useContext, useEffect, useRef, useState } from 'react'

// One WebSocket for the whole app; pages subscribe to live agent events.
const Ctx = createContext({ events: [], connected: false, subscribe: () => () => {} })

export function EventsProvider({ children }) {
  const [events, setEvents] = useState([])
  const [connected, setConnected] = useState(false)
  const listeners = useRef(new Set())

  useEffect(() => {
    let ws, timer, closed = false
    const connect = () => {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws'
      ws = new WebSocket(`${proto}://${location.host}/ws/events`)
      ws.onopen = () => setConnected(true)
      ws.onclose = () => { setConnected(false); if (!closed) timer = setTimeout(connect, 2000) }
      ws.onmessage = (m) => {
        const ev = JSON.parse(m.data)
        if (ev.type === 'hello') { setEvents(ev.recent || []); return }
        setEvents((prev) => [...prev.slice(-400), ev])
        listeners.current.forEach((fn) => fn(ev))
      }
    }
    connect()
    return () => { closed = true; clearTimeout(timer); ws && ws.close() }
  }, [])

  const subscribe = (fn) => { listeners.current.add(fn); return () => listeners.current.delete(fn) }
  return <Ctx.Provider value={{ events, connected, subscribe }}>{children}</Ctx.Provider>
}

export const useEvents = () => useContext(Ctx)

export function useOnEvent(fn, deps = []) {
  const { subscribe } = useEvents()
  useEffect(() => subscribe(fn), deps) // eslint-disable-line react-hooks/exhaustive-deps
}
