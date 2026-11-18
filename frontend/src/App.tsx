import { useEffect, useState } from 'react';
import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import { useAuth } from './context/AuthContext';
import Login from './pages/Login';
import Chat from './pages/Chat';
import Admin from './pages/Admin';
import { LoaderCircle } from 'lucide-react';
export default function App(){
 const {user,loading}=useAuth(); const location=useLocation();
 useEffect(()=>{document.title=location.pathname.startsWith('/admin')?'Cortex · Administration':'Cortex · Enterprise Intelligence';},[location.pathname]);
 if(loading)return <div className="boot"><div className="brand-mark"><span/></div><LoaderCircle className="spin" size={18}/><span>Initializing secure workspace</span></div>;
 return <Routes><Route path="/login" element={user?<Navigate to="/chat" replace/>:<Login/>}/><Route path="/chat" element={user?<Chat/>:<Navigate to="/login" replace/>}/><Route path="/admin/*" element={user?.role==='Admin'?<Admin/>:<Navigate to="/chat" replace/>}/><Route path="*" element={<Navigate to={user?'/chat':'/login'} replace/>}/></Routes>
}
