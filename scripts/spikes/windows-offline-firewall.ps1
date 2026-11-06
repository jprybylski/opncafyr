# Install spike #6: per-user outbound block for the offline variant (run elevated).
# Blocks every remote address except loopback for one local user, using the same
# -LocalUser SDDL mechanism as other per-user sandboxes. -Action add|remove|show.
param([ValidateSet('add','remove','show')][string]$Action, [string]$User = 'opncafyr-spike')
$sid = (Get-LocalUser $User).SID.Value
$name = 'opncafyr_spike_offline_block_outbound'
if ($Action -eq 'add') {
    New-NetFirewallRule -DisplayName $name -Name $name -Direction Outbound -Action Block -Profile Any `
        -LocalUser "O:LSD:(A;;CC;;;$sid)" `
        -RemoteAddress @('0.0.0.0-126.255.255.255', '128.0.0.0-255.255.255.255', '::2-ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff') | Out-Null
}
if ($Action -eq 'remove') { Remove-NetFirewallRule -Name $name -ErrorAction SilentlyContinue }
Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Select-Object Name, Enabled, Direction, Action | Format-List | Out-String
