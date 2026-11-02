import {request,downloadBlob} from './api'
export const listMasters=params=>request('/masters',{params})
export const masterDetail=code=>request(`/masters/${encodeURIComponent(code)}`)
export const masterRows=(code,params)=>request(`/masters/${encodeURIComponent(code)}/rows`,{params})
export const sourceRows=(id,params)=>request(`/sources/${id}/rows`,{params})
export const addRow=(id,body)=>request(`/sources/${id}/rows`,{method:'POST',body})
export const editRow=(id,row,body)=>request(`/sources/${id}/rows/${row}`,{method:'PUT',body})
export const deleteRow=(id,row)=>request(`/sources/${id}/rows/${row}`,{method:'DELETE'})
export const duplicates=code=>request(`/masters/${encodeURIComponent(code)}/duplicates`)
export const history=(id,page=1,page_size=20)=>request(`/sources/${id}/history`,{params:{page,page_size}})
export const previewImport=(id,file,mode,mapping)=>{const body=new FormData();body.append('file',file);body.append('mode',mode);body.append('mapping',JSON.stringify(mapping));return request(`/sources/${id}/import/preview`,{method:'POST',body,isForm:true})}
export const executeImport=(id,body)=>request(`/sources/${id}/import/execute`,{method:'POST',body})
export async function download(path,name,params){downloadBlob(await request(path,{params,isBlob:true}),name)}

export const reviewRow=(id,body)=>request(`/sources/${id}/rows/review`,{method:"POST",body})
export const commitRow=(id,body)=>request(`/sources/${id}/rows/commit`,{method:"POST",body})
