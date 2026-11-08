const { app, BrowserWindow, ipcMain, desktopCapturer, dialog } = require('electron');
const path = require('node:path');
const fs = require('node:fs/promises');
function createWindow() {
  const win = new BrowserWindow({width:1180,height:800,minWidth:900,minHeight:650,backgroundColor:'#080b10',title:'BLOODFANG RECORDING',webPreferences:{preload:path.join(__dirname,'preload.js'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  win.loadFile(path.join(__dirname,'index.html'));
}
app.whenReady().then(()=>{
  ipcMain.handle('recording:sources',async()=>{
    const sources=await desktopCapturer.getSources({types:['screen','window'],thumbnailSize:{width:320,height:180}});
    return sources.map(s=>({id:s.id,name:s.name,thumbnail:s.thumbnail.toDataURL()}));
  });
  ipcMain.handle('recording:save',async(_event,{buffer,suggestedName})=>{
    const {canceled,filePath}=await dialog.showSaveDialog({defaultPath:suggestedName||'bloodfang-clip.webm',filters:[{name:'WebM Video',extensions:['webm']}]});
    if(canceled||!filePath)return {canceled:true};
    await fs.writeFile(filePath,Buffer.from(buffer));return {canceled:false,filePath};
  });
  createWindow();app.on('activate',()=>{if(BrowserWindow.getAllWindows().length===0)createWindow();});
});
app.on('window-all-closed',()=>{if(process.platform!=='darwin')app.quit();});