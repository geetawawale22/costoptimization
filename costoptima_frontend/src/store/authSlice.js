import { createSlice } from '@reduxjs/toolkit'

const authSlice = createSlice({
  name: 'auth',
  initialState: { user: null, permissions: [], loading: true },
  reducers: {
    setAuth(state, action) {
      state.user = action.payload.user
      state.permissions = action.payload.permissions || []
      state.loading = false
    },
    clearAuth(state) {
      state.user = null
      state.permissions = []
      state.loading = false
    },
  },
})

export const { setAuth, clearAuth } = authSlice.actions
export default authSlice.reducer