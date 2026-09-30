import { configureStore } from '@reduxjs/toolkit'
import authReducer from './authSlice'
import mergeReducer from './mergeSlice'

export const store = configureStore({
  reducer: {
    auth: authReducer,
    merge: mergeReducer,
  },
})