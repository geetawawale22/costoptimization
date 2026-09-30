export default function StatusChip({ value }) {
  const v = String(value)
  let cls = 'gray'
  if (['Completed', 'Success', 'Active', 'Valid', 'Yes', 'Passed'].includes(v)) cls = 'green'
  else if (['Running', 'Info', 'Queued'].includes(v)) cls = 'blue'
  else if (['Failed', 'Error', 'Invalid', 'No'].includes(v)) cls = 'red'
  else if (['Warning', 'Needs Review', 'Pending'].includes(v)) cls = 'amber'
  return <span className={`chip ${cls}`}>{v}</span>
}
