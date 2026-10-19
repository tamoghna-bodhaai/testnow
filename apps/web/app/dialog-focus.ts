"use client";
import {useEffect,useRef} from "react";
const locks=new Map<HTMLElement,{count:number;previous:boolean}>();
let bodyLocks=0,bodyOverflow="";
export function useDialogFocus(active:boolean,onEscape:()=>void,returnFocus?:()=>HTMLElement|null){
 const root=useRef<HTMLDivElement>(null),escape=useRef(onEscape),fallback=useRef(returnFocus);escape.current=onEscape;fallback.current=returnFocus;
 useEffect(()=>{
  const dialog=root.current;if(!active||!dialog)return;
  const previous=document.activeElement as HTMLElement|null;
  const locked:HTMLElement[]=[];let current:HTMLElement=dialog;
  while(current.parentElement){for(const sibling of Array.from(current.parentElement.children)){if(sibling!==current&&sibling instanceof HTMLElement){const record=locks.get(sibling)||{count:0,previous:sibling.inert};record.count++;locks.set(sibling,record);sibling.inert=true;locked.push(sibling);}}if(current.parentElement===document.body)break;current=current.parentElement;}
  if(bodyLocks++===0){bodyOverflow=document.body.style.overflow;document.body.style.overflow="hidden";}
  dialog.dataset.focusDialog="true";
  const focusable=()=>Array.from(dialog.querySelectorAll<HTMLElement>('button,a[href],input,select,textarea,[tabindex]:not([tabindex="-1"])')).filter(element=>!element.matches(':disabled')&&!element.closest('[inert]')&&element.getClientRects().length>0);
  (focusable()[0]||dialog).focus();
  const keydown=(event:KeyboardEvent)=>{
   const dialogs=Array.from(document.querySelectorAll('[data-focus-dialog="true"]'));if(dialogs.at(-1)!==dialog)return;
   if(event.key==="Escape"){event.preventDefault();escape.current();return;}
   if(event.key!=="Tab")return;const elements=focusable();if(!elements.length){event.preventDefault();dialog.focus();return;}
   const first=elements[0],last=elements[elements.length-1];if(event.shiftKey&&(document.activeElement===first||document.activeElement===dialog)){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
  };
  document.addEventListener('keydown',keydown,true);
  return()=>{delete dialog.dataset.focusDialog;document.removeEventListener('keydown',keydown,true);for(const element of locked){const record=locks.get(element);if(record&&--record.count===0){element.inert=record.previous;locks.delete(element);}}if(--bodyLocks===0)document.body.style.overflow=bodyOverflow;requestAnimationFrame(()=>{const usable=previous?.isConnected&&previous!==document.body&&previous!==document.documentElement&&!previous.closest('[inert]');const target=fallback.current?.()||(usable?previous:null);if(target?.isConnected&&!target.closest('[inert]'))target.focus({preventScroll:true});});};
 },[active]);
 return root;
}
