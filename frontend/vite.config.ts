import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
 plugins:[react()],
 server:{host:'127.0.0.1',port:5173,strictPort:true,proxy:{'/api':{
  target:'http://127.0.0.1:8877',changeOrigin:true,
  configure(proxy){proxy.on('proxyReq',(out,req)=>{
   if(req.headers.origin==='http://127.0.0.1:5173')out.setHeader('origin','http://127.0.0.1:8877');
  });}
 }}},
 build:{outDir:'dist',emptyOutDir:true}
});
