import { usePageCrumb } from '../components/Layout'

export default function ConverterPlaceholder({ title }) {
  usePageCrumb(`Converter / <b>${title}</b>`)

  return (
    <div style={{ padding: 40 }}>
      <h1 className="page-title">{title}</h1>
      <p className="page-sub">This converter flow is not configured yet.</p>
    </div>
  )
}
