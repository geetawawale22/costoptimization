import { createSlice } from '@reduxjs/toolkit'

/** Tracks the last Master Rate Data merge result app-wide, e.g. so a sidebar badge or
    another page can show "last merged 3 minutes ago" without re-fetching. */
const mergeSlice = createSlice({
  name: 'merge',
  initialState: { running: false, lastResult: null },
  reducers: {
    mergeStarted(state) {
      state.running = true
    },
    mergeFinished(state, action) {
      state.running = false
      state.lastResult = action.payload
    },
  },
})

export const { mergeStarted, mergeFinished } = mergeSlice.actions
export default mergeSlice.reducer