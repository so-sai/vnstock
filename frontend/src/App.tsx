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
            fontFamily: 'Inter, sans-serif',
            fontSize: '14px',
          },
        }}
      />
      <ErrorBoundary>
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
          </Route>
        </Routes>
      </ErrorBoundary>
    </QueryClientProvider>
  )
}

export default App
