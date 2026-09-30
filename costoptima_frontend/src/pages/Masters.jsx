import { useParams } from 'react-router-dom'
import WorkbookTable from '../components/WorkbookTable'
import { MASTERS_PAGES } from '../config/commodityStructure'
import { usePageCrumb } from '../components/Layout'

export default function Masters() {
  const { typeCode } = useParams()
  const page = MASTERS_PAGES.find((m) => m.code === typeCode)
  usePageCrumb(`Masters / <b>${page?.label || typeCode}</b>`)

  return (
    <WorkbookTable
      typeCode={typeCode}
      title={page?.label || typeCode}
      subtitle="Shared master data used across the cost engine."
    />
  )
}
