// token is kept in localStorage and checked against /auth/me on mount,
// it can expire while the tab is closed. sign-out is local only

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'

import { api, getStoredToken, storeToken } from '@/api/client'
import type { LoginPayload, RegisterPayload, TokenResponse, User } from '@/api/types'

interface AuthContextValue {
  user: User | null
  // true until the stored token is checked, stops a redirect on first paint
  isLoading: boolean
  signIn: (payload: LoginPayload) => Promise<User>
  signUp: (payload: RegisterPayload) => Promise<User>
  signOut: () => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    const token = getStoredToken()
    if (!token) {
      setIsLoading(false)
      return
    }

    let cancelled = false
    api
      .get<User>('/api/v1/auth/me')
      .then((me) => {
        if (!cancelled) setUser(me)
      })
      .catch(() => {
        // expired or tampered, drop it
        storeToken(null)
        if (!cancelled) setUser(null)
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  const applyToken = useCallback((response: TokenResponse): User => {
    storeToken(response.access_token)
    setUser(response.user)
    return response.user
  }, [])

  const signIn = useCallback(
    async (payload: LoginPayload) => {
      const response = await api.post<TokenResponse>('/api/v1/auth/login', payload, {
        authenticated: false,
      })
      return applyToken(response)
    },
    [applyToken],
  )

  const signUp = useCallback(
    async (payload: RegisterPayload) => {
      const response = await api.post<TokenResponse>('/api/v1/auth/register', payload, {
        authenticated: false,
      })
      return applyToken(response)
    },
    [applyToken],
  )

  const signOut = useCallback(() => {
    storeToken(null)
    setUser(null)
  }, [])

  const value = useMemo(
    () => ({ user, isLoading, signIn, signUp, signOut }),
    [user, isLoading, signIn, signUp, signOut],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext)
  if (context === null) {
    throw new Error('useAuth must be used inside <AuthProvider>.')
  }
  return context
}
