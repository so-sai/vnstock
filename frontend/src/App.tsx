import { useState, useEffect } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'sonner'
import { Routes, Route } from 'react-router-dom'
import AppLayout from './components/AppLayout'
import ErrorBoundary from './components/ErrorBoundary'
import MacroDashboard from './pages/MacroDashboard'
import ScreenerPage from './pages/ScreenerPage'
import AlphaModelsPage from './pages/AlphaModelsPage'
import TimeKernelPage from './pages/TimeKernelPage'
import PortfolioPage from './pages/PortfolioPage'
import PortfolioObservatoryPage from './pages/PortfolioObservatoryPage'
import SectorBreadthPage from './pages/SectorBreadthPage'
import ReplayTimelinePage from './pages/ReplayTimelinePage'
import ActionableIntelligencePage from './pages/ActionableIntelligencePage'
import WeeklyCognitiveReport from './pages/WeeklyCognitiveReport'
import PaperTradingPage from './pages/PaperTradingPage'
import EpistemicDashboard from './pages/EpistemicDashboard'

// ==============================================================================
// WHY: The Tauri sidecar spawns uv_backend.exe serve asynchronously. The Rust
// sidecar spawn returns immediately (child process created) but the Python
// server takes 1-5 seconds to boot (uvicorn + import chain). Without this gate,
// every API call fails with "Failed to fetch" and the entire UI is white.
// BOUNDARY: Block rendering until backend responds on /api/session-info.
// ==============================================================================
const API_BASE = import.meta.env.DEV ? '/api' : 'http://localhost:17039/api'
const HEALTH_URL = `${API_BASE}/session-info`
const MAX_RETRIES = 15
const RETRY_DELAY = 2000

function BackendGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false)
  const [retries, setRetries] = useState(0)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout>

    async function check() {
      try {
        const res = await fetch(HEALTH_URL, { signal: AbortSignal.timeout(3000) })
        if (res.ok && !cancelled) {
          setReady(true)
          return
        }
      } catch { /* retry */ }
      if (!cancelled) {
        setRetries(r => r + 1)
        if (retries + 1 < MAX_RETRIES) {
          timer = setTimeout(check, RETRY_DELAY)
        }
      }
    }

    check()

    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

  if (!ready) {
    const pct = Math.min(Math.round((retries / MAX_RETRIES) * 100), 99)
    return (
      <div style={{
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        height: '100vh', fontFamily: 'system-ui, sans-serif', background: '#0a0a0f', color: '#c0c0d0'
      }}>
        <div style={{ fontSize: '28px', marginBottom: '8px' }}>⏳</div>
        <div style={{ fontSize: '18px', fontWeight: 600, marginBottom: '4px' }}>Đang kết nối Backend...</div>
        <div style={{ fontSize: '14px', color: '#888' }}>{retries < MAX_RETRIES ? `Thử lần ${retries + 1}/${MAX_RETRIES}` : 'Backend không phản hồi'}</div>
        {retries >= MAX_RETRIES && (
          <div style={{ marginTop: '16px', fontSize: '14px', color: '#f87171', textAlign: 'center', maxWidth: '400px' }}>
            Không thể kết nối đến backend server.<br />
            Hãy kiểm tra tiến trình uv_backend.exe hoặc chạy lại ứng dụng.
          </div>
        )}
        <div style={{ marginTop: '24px', width: '200px', height: '4px', background: '#222', borderRadius: '2px', overflow: 'hidden' }}>
          <div style={{ width: `${pct}%`, height: '100%', background: '#60a5fa', borderRadius: '2px', transition: 'width 0.3s' }} />
        </div>
      </div>
    )
  }

  return <>{children}</>
}

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
})

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <Toaster
        position="top-right"
        expand={false}
        richColors
        closeButton
        toastOptions={{
          style: {
            fontFamily: 'system-ui, sans-serif',
            fontSize: '14px',
          },
        }}
      />
      <ErrorBoundary>
        <BackendGate>
          <Routes>
            <Route path="/" element={<AppLayout />}>
              <Route index element={<MacroDashboard />} />
              <Route path="nhip-dap-vi-mo" element={<MacroDashboard />} />
              <Route path="bo-loc-kim-cuong" element={<ScreenerPage />} />
              <Route path="mo-hinh-alpha" element={<AlphaModelsPage />} />
              <Route path="kiem-chung-lich-su" element={<TimeKernelPage />} />
              <Route path="so-tay-danh-muc" element={<PortfolioPage />} />
              <Route path="quan-tri-danh-muc" element={<PortfolioObservatoryPage />} />
              <Route path="do-rong-thi-truong" element={<SectorBreadthPage />} />
              <Route path="duong-chi-lich-su" element={<ReplayTimelinePage />} />
              <Route path="trung-tam-hanh-dong" element={<ActionableIntelligencePage />} />
              <Route path="bao-cao-tuan" element={<WeeklyCognitiveReport />} />
              <Route path="paper-trading" element={<PaperTradingPage />} />
              <Route path="epistemic" element={<EpistemicDashboard />} />
            </Route>
          </Routes>
        </BackendGate>
      </ErrorBoundary>
    </QueryClientProvider>
  )
}

export default App
