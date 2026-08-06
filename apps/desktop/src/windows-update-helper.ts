/**
 * Windows PowerShell 5.1 updater UI. It runs outside Electron so the progress
 * window stays visible while NSIS replaces the application files.
 */
export function windowsUpdateHelperScript(): string {
  return String.raw`param(
  [int]$PidToWait,
  [string]$Installer,
  [string]$LogPath,
  [string]$ReadyPath,
  [string]$StatePath,
  [string]$StagingPath,
  [string]$InstallDir,
  [string]$InstalledExe
)

$ErrorActionPreference = "Stop"
try {
  "[$([DateTime]::UtcNow.ToString('o'))] Windows updater process started" | Out-File -FilePath $LogPath -Append -Encoding utf8
} catch {}

function Write-UpdateLog([string]$Message) {
  try {
    "[$([DateTime]::UtcNow.ToString('o'))] $Message" | Out-File -FilePath $LogPath -Append -Encoding utf8
  } catch {}
}

function Write-UpdateState([string]$Phase, [string]$Message) {
  try {
    $value = [ordered]@{
      schema = 1
      phase = $Phase
      message = $Message
      updatedAt = [DateTime]::UtcNow.ToString('o')
    }
    $value | ConvertTo-Json -Compress | Set-Content -LiteralPath $StatePath -Encoding utf8
  } catch {}
}

try {
  Add-Type -AssemblyName PresentationFramework
  Add-Type -AssemblyName PresentationCore
  Add-Type -AssemblyName WindowsBase

  [xml]$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="VideoAgents 更新" Width="520" Height="255"
        WindowStartupLocation="CenterScreen" ResizeMode="NoResize"
        ShowInTaskbar="True" Topmost="True" Background="#111318"
        Foreground="#F3F4F6" FontFamily="Segoe UI">
  <Grid Margin="32,28,32,24">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto" />
      <RowDefinition Height="Auto" />
      <RowDefinition Height="Auto" />
      <RowDefinition Height="*" />
      <RowDefinition Height="Auto" />
    </Grid.RowDefinitions>
    <TextBlock Grid.Row="0" Text="正在更新 VideoAgents" FontSize="19" FontWeight="SemiBold" />
    <TextBlock x:Name="StatusText" Grid.Row="1" Margin="0,20,0,12"
               Text="正在准备安装新版本……" FontSize="14" />
    <ProgressBar x:Name="UpdateProgress" Grid.Row="2" Height="12" IsIndeterminate="True" />
    <TextBlock x:Name="DetailText" Grid.Row="3" Margin="0,13,0,0"
               Text="安装过程中请勿关闭电脑。" Foreground="#9CA3AF"
               FontSize="12" TextWrapping="Wrap" />
    <StackPanel x:Name="FailureActions" Grid.Row="4" Margin="0,14,0,0"
                Orientation="Horizontal" HorizontalAlignment="Right" Visibility="Collapsed">
      <Button x:Name="LogButton" Content="查看日志" MinWidth="82" Height="30" Margin="0,0,8,0" />
      <Button x:Name="ManualButton" Content="手动安装" MinWidth="82" Height="30" Margin="0,0,8,0" />
      <Button x:Name="RetryButton" Content="重试" MinWidth="82" Height="30" IsDefault="True" />
    </StackPanel>
  </Grid>
</Window>
"@

  $reader = New-Object System.Xml.XmlNodeReader $xaml
  $script:window = [Windows.Markup.XamlReader]::Load($reader)
  $script:statusText = $script:window.FindName("StatusText")
  $script:detailText = $script:window.FindName("DetailText")
  $script:progress = $script:window.FindName("UpdateProgress")
  $script:failureActions = $script:window.FindName("FailureActions")
  $script:logButton = $script:window.FindName("LogButton")
  $script:manualButton = $script:window.FindName("ManualButton")
  $script:retryButton = $script:window.FindName("RetryButton")
  $script:allowClose = $false
  $script:state = "waiting"
  $script:installerProcess = $null
  $script:waitDeadline = [DateTime]::UtcNow.AddSeconds(120)
  $script:closeAt = [DateTime]::MaxValue

  function Set-UpdateStatus([string]$Status, [string]$Detail) {
    $script:statusText.Text = $Status
    $script:detailText.Text = $Detail
  }

  function Show-UpdateFailure([string]$Message) {
    Write-UpdateLog "Update failed: $Message"
    Write-UpdateState "failed" $Message
    $script:state = "failed"
    $script:progress.IsIndeterminate = $false
    $script:progress.Value = 0
    $script:failureActions.Visibility = "Visible"
    $script:allowClose = $true
    Set-UpdateStatus "更新安装失败" ($Message + [Environment]::NewLine + "可以重试，或打开安装程序手动完成更新。")
    $script:window.Topmost = $false
    $script:window.Activate() | Out-Null
  }

  function Start-SilentInstaller {
    $script:state = "installing"
    $script:failureActions.Visibility = "Collapsed"
    $script:allowClose = $false
    $script:window.Topmost = $true
    $script:window.Activate() | Out-Null
    $script:progress.IsIndeterminate = $true
    Set-UpdateStatus "正在安装新版本……" "安装过程中请勿关闭电脑。完成后将自动启动 VideoAgents。"
    Write-UpdateState "installing" "Installing the new VideoAgents version"
    Write-UpdateLog "Starting silent installer: $Installer"
    $arguments = @("--updated", "/S", "--force-run", "/D=$InstallDir")
    $script:installerProcess = Start-Process -FilePath $Installer -ArgumentList $arguments -PassThru
    if ($null -eq $script:installerProcess) { throw "The installer process did not start" }
    Write-UpdateLog "Installer process started with PID $($script:installerProcess.Id)"
  }

  $script:window.Add_Closing({
    param($sender, $eventArgs)
    if (-not $script:allowClose) { $eventArgs.Cancel = $true }
  })

  $script:retryButton.Add_Click({
    try {
      Start-SilentInstaller
    } catch {
      Show-UpdateFailure $_.Exception.Message
    }
  })

  $script:manualButton.Add_Click({
    try {
      Write-UpdateLog "Starting visible installer for manual recovery"
      Write-UpdateState "manual" "Starting the visible installer"
      Start-Process -FilePath $Installer -ArgumentList @("--updated", "--force-run") | Out-Null
      $script:state = "manual"
      $script:allowClose = $true
      $script:window.Close()
    } catch {
      Show-UpdateFailure $_.Exception.Message
    }
  })

  $script:logButton.Add_Click({
    try {
      if (Test-Path -LiteralPath $LogPath) { Invoke-Item -LiteralPath $LogPath }
    } catch {
      $script:detailText.Text = "无法打开日志：$($_.Exception.Message)" + [Environment]::NewLine + "日志位置：$LogPath"
    }
  })

  $script:timer = New-Object System.Windows.Threading.DispatcherTimer
  $script:timer.Interval = [TimeSpan]::FromMilliseconds(300)
  $script:timer.Add_Tick({
    try {
      if ($script:state -eq "waiting") {
        if ([DateTime]::UtcNow -ge $script:waitDeadline) {
          throw "等待旧版 VideoAgents 退出超时"
        }
        if ($null -eq (Get-Process -Id $PidToWait -ErrorAction SilentlyContinue)) {
          Write-UpdateLog "VideoAgents process $PidToWait exited"
          Start-SilentInstaller
        }
        return
      }

      if ($script:state -eq "installing") {
        if ($null -ne $script:installerProcess -and $script:installerProcess.HasExited) {
          $exitCode = $script:installerProcess.ExitCode
          Write-UpdateLog "Installer exited with code $exitCode"
          if ($exitCode -ne 0) { throw "安装程序退出码：$exitCode" }
          try {
            Remove-Item -LiteralPath $StagingPath -Recurse -Force -ErrorAction Stop
            Write-UpdateLog "Removed update staging directory"
          } catch {
            Write-UpdateLog "Could not remove update staging directory: $($_.Exception.Message)"
          }
          $script:state = "launching"
          $script:progress.IsIndeterminate = $false
          $script:progress.Value = 100
          $script:closeAt = [DateTime]::UtcNow.AddSeconds(3)
          Set-UpdateStatus "安装完成，正在启动新版本……" "VideoAgents 将自动重新打开。"
          Write-UpdateState "launching" "Installation completed; starting VideoAgents"
          if (-not (Test-Path -LiteralPath $InstalledExe -PathType Leaf)) { throw "Installed app not found: $InstalledExe" }
          Start-Process -FilePath $InstalledExe | Out-Null
        }
        return
      }

      if ($script:state -eq "launching" -and [DateTime]::UtcNow -ge $script:closeAt) {
        $script:state = "completed"
        Write-UpdateState "completed" "VideoAgents update completed"
        Write-UpdateLog "Update completed successfully"
        $script:allowClose = $true
        $script:timer.Stop()
        $script:window.Close()
      }
    } catch {
      Show-UpdateFailure $_.Exception.Message
    }
  })

  $script:window.Add_ContentRendered({
    try {
      Write-UpdateLog "Updater window is ready; waiting for VideoAgents process $PidToWait"
      Write-UpdateState "waiting" "Waiting for the old VideoAgents process to exit"
      [System.IO.File]::WriteAllText($ReadyPath, "ready")
      $script:timer.Start()
    } catch {
      Write-UpdateLog "Updater initialization failed: $($_.Exception.Message)"
      $script:allowClose = $true
      $script:window.Close()
    }
  })

  $script:window.ShowDialog() | Out-Null
  if ($script:state -eq "completed" -or $script:state -eq "manual") { exit 0 }
  exit 1
} catch {
  try { Write-UpdateLog "Updater crashed: $($_.Exception.Message)" } catch {}
  exit 1
}
`
}
