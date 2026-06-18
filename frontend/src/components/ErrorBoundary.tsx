import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
  onError?: (error: Error, errorInfo: ErrorInfo) => void
}

interface State {
  hasError: boolean
  error: Error | null
}

class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.warn('[ErrorBoundary]', error.message, errorInfo.componentStack)
    this.props.onError?.(error, errorInfo)
  }

  render() {
    if (this.state.hasError) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div className="flex items-center justify-center h-64 text-japandi-muted-clay">
          <div className="text-center">
            <div className="text-3xl mb-2">⚠️</div>
            <p className="text-sm font-mono">Lõi kết nối đang bận hoặc chưa khởi động</p>
            <button
              onClick={() => this.setState({ hasError: false, error: null })}
              className="mt-3 text-xs underline hover:text-japandi-charcoal"
            >
              Thử lại
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

export default ErrorBoundary
