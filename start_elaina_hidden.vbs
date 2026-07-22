Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

projectFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
batchFile = projectFolder & "\start_elaina.bat"

' Run the batch file invisibly.
shell.Run """" & batchFile & """", 0, False