import React, {useEffect, useState} from 'react'
import {NavLink, useLocation} from 'react-router-dom'
import {useAuth} from '../auth/AuthContext'
export function SidebarIcon({name}) {
 const shapes={
  calculator:<><rect x="4" y="2" width="16" height="20" rx="3"/><path d="M8 6h8M8 11h1m6 0h1m-8 4h1m6 0h1m-8 4h1m6 0h1"/></>,
  data:<><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
  exchange:<><path d="M4 7h16m-4-4 4 4-4 4M20 17H4m4-4-4 4 4 4"/></>,
  activity:<><path d="M3 12h4l3-8 4 16 3-8h4"/></>,
  formula:<><path d="M18 3h-7l-4 18M5 8h11M13 13l7 7m0-7-7 7"/></>,
  users:<><circle cx="9" cy="7" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M17 4a3 3 0 0 1 0 6m1 5a5 5 0 0 1 3 5"/></>,
  shield:<><path d="m12 2 8 4v6c0 5-8 10-8 10S4 17 4 12V6Z"/><path d="m8 12 3 3 5-6"/></>,
  settings:<><path d="m9 3-1 3-3 1v4l2 1-2 2 2 4 3-1 2 3 3-2v-3l4-1 1-4-3-2V4l-4-2-2 2Z"/><circle cx="12" cy="11" r="3"/></>,
  profile:<><circle cx="12" cy="8" r="4"/><path d="M4 22v-3a8 8 0 0 1 16 0v3"/></>,
  chevron:<path d="m9 5 7 7-7 7"/>,collapse:<path d="m15 5-7 7 7 7"/>,expand:<path d="m8 5 7 7-7 7"/>,pilot:<><path d="m3 11 18-8-8 18-2-8Z"/><path d="m11 13 10-10"/></>
 }
 return <svg className="sidebar-icon" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shapes[name]||shapes.pilot}</svg>
}
function Item({to,icon,label}){return <NavLink to={to} className={({isActive})=>`nav-item${isActive?' active':''}`} title={label}><span className="ic"><SidebarIcon name={icon}/></span><span className="nav-label">{label}</span></NavLink>}
export default function Sidebar({collapsed,onToggleCollapse}){
 const {can}=useAuth(),location=useLocation(),[open,setOpen]=useState(location.pathname.startsWith('/settings')||location.pathname==='/formula-management')
 useEffect(()=>{if(location.pathname.startsWith('/settings')||location.pathname==='/formula-management')setOpen(true)},[location.pathname])
 const admin=can('users','view')||can('roles','view')||can('settings','view')
 return <aside className={`sidebar${collapsed?' collapsed':''}`} aria-label="Sidebar"><LinkBrand/><button className="sidebar-toggle" type="button" onClick={onToggleCollapse} title={collapsed?'Expand sidebar':'Collapse sidebar'} aria-label={collapsed?'Expand sidebar':'Collapse sidebar'} aria-expanded={!collapsed} aria-controls="primary-navigation"><SidebarIcon name={collapsed?'expand':'collapse'}/><span className="nav-label">Collapse sidebar</span></button><nav id="primary-navigation" className="sidebar-scroll" aria-label="Main navigation"><div className="nav-section-label">Workspace</div><div className="nav-tree">
 {can('boms','view')&&<Item to="/calculations" icon="calculator" label="Calculations"/>}{can('masters','view')&&<Item to="/masters" icon="data" label="Master Data"/>}{can('converters','view')&&<Item to="/ftg-converter" icon="exchange" label="FTG Converter"/>}{can('settings','view')&&<Item to="/activity-logs" icon="activity" label="Activity Logs"/>}
 {admin&&<><div className="nav-section-label">Manage</div><button type="button" className="nav-item" onClick={()=>{if(collapsed){onToggleCollapse();setOpen(true)}else setOpen(!open)}} title="Administration" aria-expanded={open}><span className="ic"><SidebarIcon name="settings"/></span><span className="nav-label">Administration</span><span className={`chev${open?' open':''}`}><SidebarIcon name="chevron"/></span></button><div className="nav-children lvl1" style={{display:open?'block':'none'}}>{can('users','view')&&<Item to="/settings/users" icon="users" label="Users"/>}{can('roles','view')&&<Item to="/settings/roles-permissions" icon="shield" label="Roles & Permissions"/>}{can('settings','view')&&<><Item to="/formula-management" icon="formula" label="Formula Management"/><Item to="/settings/app-configuration" icon="settings" label="Application Settings"/></>}</div></>}
 </div><div className="nav-bottom"><Item to="/profile" icon="profile" label="My Profile"/></div></nav><div className="sidebar-footer"><span className="nav-label">Cost Excellence, delivered.</span></div></aside>
}
function LinkBrand(){return <div className="brand"><div className="brand-icon"><SidebarIcon name="pilot"/></div><div className="brand-text"><b>Cost Pilot</b><span>Cost Excellence Delivered</span></div></div>}
