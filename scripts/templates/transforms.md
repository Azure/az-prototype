# Log Analytics
Automatic corrections for Log Analytics workspace ARM property placement

**Domain:** `monitoring`

<hr />

### Checks (1)

<table>
<thead>
<tr>
<th width="185">Check</th><th>Description</th>
</tr>
</thead>
<tbody>
<tr><td><a href="#TFM-LA-001">TFM-LA-001</a></td><td>Move disableLocalAuth from inside features block to properties root</td></tr>
</tbody>
</table>

<hr />

## TFM-LA-001
Move disableLocalAuth from inside features block to properties root

**Rationale:** ARM silently drops disableLocalAuth if nested inside properties.features. The property must be a direct child of properties for Log Analytics workspaces.  
**Agents:** `terraform-agent, bicep-agent`

### Targets

<ul><li>Microsoft.OperationalInsights/workspaces</li></ul>

**Type:** Regex  
**Search:** `'(features\s*=\s*\{[^}]*?)(\s*disableLocalAuth\s*=\s*\w+\s*\n?)([^}]*\})'`  
**Replace:** `'\1\3'`

<hr />
