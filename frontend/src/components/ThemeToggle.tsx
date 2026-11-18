import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';
export default function ThemeToggle(){
 const [light,setLight]=useState(()=>localStorage.getItem('cortex_theme')==='light');
 useEffect(()=>{document.documentElement.dataset.theme=light?'light':'dark';localStorage.setItem('cortex_theme',light?'light':'dark')},[light]);
 return <button className="icon-button theme-toggle" onClick={()=>setLight(v=>!v)} title={`Switch to ${light?'dark':'light'} theme`} aria-label={`Switch to ${light?'dark':'light'} theme`}>{light?<Moon size={16}/>:<Sun size={16}/>}</button>
}
