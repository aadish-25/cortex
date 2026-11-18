import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { api, post } from '../lib/api';
export type User = { id:string; name:string; username:string; email:string; job_title:string; role:'Admin'|'Manager'|'Employee'; department:string; clearance_level:string; is_active:boolean };
type AuthValue = { user:User|null; loading:boolean; login:(identifier:string,password:string)=>Promise<void>; logout:()=>Promise<void>; refresh:()=>Promise<void> };
const AuthContext = createContext<AuthValue>(null!);
export function AuthProvider({children}:{children:ReactNode}) {
 const [user,setUser]=useState<User|null>(null); const [loading,setLoading]=useState(true);
 const refresh=async()=>{ try{setUser(await api<User>('/api/v1/auth/me'));}catch{setUser(null);}finally{setLoading(false);} };
 useEffect(()=>{refresh(); const fn=()=>setUser(null); window.addEventListener('cortex:unauthorized',fn); return()=>window.removeEventListener('cortex:unauthorized',fn);},[]);
 const login=async(identifier:string,password:string)=>{const r=await post<{access_token:string;user:User}>('/api/v1/auth/login',{identifier,password}); sessionStorage.setItem('cortex_token',r.access_token); setUser(r.user);};
 const logout=async()=>{try{await post('/api/v1/auth/logout');}finally{sessionStorage.removeItem('cortex_token');setUser(null);}};
 return <AuthContext.Provider value={{user,loading,login,logout,refresh}}>{children}</AuthContext.Provider>;
}
export const useAuth=()=>useContext(AuthContext);
