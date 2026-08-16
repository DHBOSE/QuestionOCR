Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
bat = fso.GetParentFolderName(WScript.ScriptFullName) & "\Æô¶¯.bat"
sh.Run """" & bat & """ hidden", 0, False
