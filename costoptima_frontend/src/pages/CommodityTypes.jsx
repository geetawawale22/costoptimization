import { useParams } from 'react-router-dom'
import WorkbookTable from '../components/WorkbookTable'
import { COMMODITY_STRUCTURE, categoryLabel, PCM_MASTER_CODE, workbookCodeFor } from '../config/commodityStructure'
import { usePageCrumb } from '../components/Layout'

export default function CommodityTypes() {
  const { commodityCode, category } = useParams()

  if (commodityCode === PCM_MASTER_CODE || !category) {
    usePageCrumb('Commodity Types / <b>PCM Master</b>')
    return (
      <WorkbookTable
        typeCode={PCM_MASTER_CODE}
        title="PCM Master"
        subtitle="Default master data files shown across every commodity. Select a sheet above to view or edit its data."
      />
    )
  }

  const commodity = COMMODITY_STRUCTURE.find((c) => c.code === commodityCode)
  const typeCode = workbookCodeFor(commodityCode, category)
  const label = categoryLabel(category, commodity?.name || commodityCode)
  usePageCrumb(`Commodity Types / PCM Master / ${commodity?.name || commodityCode} / <b>${label}</b>`)

  const desc =
    category === 'self'
      ? `Primary master sheets for ${commodity?.name}.`
      : category === 'master_user'
        ? `Master user reference data for ${commodity?.name}.`
        : `PCE-specific reference data for ${commodity?.name}.`

  return (
    <WorkbookTable
      typeCode={typeCode}
      title={`${commodity?.name || commodityCode} ${label}`}
      subtitle={`${desc} Files are never listed in the sidebar, only here on the page.`}
    />
  )
}
