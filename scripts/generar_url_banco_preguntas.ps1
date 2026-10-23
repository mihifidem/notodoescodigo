[CmdletBinding()]
param(
    [string]$BaseUrl,
    [ValidateSet('Categoria', 'Modulo')]
    [string]$Modo,
    [int]$Categoria,
    [string]$Subcategoria,
    [int]$Modulo,
    [int]$UnidadFormativa,
    [int]$Dificultad,
    [int]$Cantidad
)

$ErrorActionPreference = 'Stop'

function Read-IntegerInRange {
    param(
        [string]$Label,
        [int]$Minimum,
        [int]$Maximum
    )
    while ($true) {
        $text = Read-Host "$Label ($Minimum-$Maximum)"
        $number = 0
        if ([int]::TryParse($text, [ref]$number) -and $number -ge $Minimum -and $number -le $Maximum) {
            return $number
        }
        Write-Warning "Introduce un entero entre $Minimum y $Maximum."
    }
}

function Get-LocalKnowledgeCatalog {
    $root = Split-Path -Parent $PSScriptRoot
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw "No se encuentra el Python del proyecto: $python"
    }
    $code = @'
import json
import os
import sys
sys.path.insert(0, sys.argv[1])
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
import django
django.setup()
from apps.courses.models import CourseCategory
from apps.duckyclash.models import ChallengeSubcategory
print(json.dumps({
    "categories": list(CourseCategory.objects.filter(activa=True).order_by("nombre", "pk").values("id", "nombre")),
    "subcategories": list(ChallengeSubcategory.objects.filter(active=True, category__activa=True).order_by("name", "pk").values("id", "name", "category_id")),
}, ensure_ascii=True))
'@
    $output = $code | & $python - $root
    if ($LASTEXITCODE -ne 0) {
        throw 'No se pudo cargar el catalogo de Django. Revisa la configuracion y la conexion a la base de datos.'
    }
    return ($output | ConvertFrom-Json)
}

if (-not $PSBoundParameters.ContainsKey('BaseUrl')) {
    $BaseUrl = Read-Host 'URL base del portal [http://127.0.0.1:8000]'
    if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
        $BaseUrl = 'http://127.0.0.1:8000'
    }
}

$baseUri = $null
if (-not [Uri]::TryCreate($BaseUrl.Trim(), [UriKind]::Absolute, [ref]$baseUri) -or
    $baseUri.Scheme -notin @('http', 'https') -or
    $baseUri.UserInfo -or $baseUri.Query -or $baseUri.Fragment) {
    throw 'La URL base debe ser HTTP/HTTPS, sin credenciales, parametros ni fragmento.'
}

if (-not $Modo) {
    while ($true) {
        $selection = Read-Host 'Filtrar por: 1 = categoria; 2 = modulo/unidad formativa'
        if ($selection -eq '1') { $Modo = 'Categoria'; break }
        if ($selection -eq '2') { $Modo = 'Modulo'; break }
        Write-Warning 'Selecciona 1 o 2.'
    }
}

$filters = [ordered]@{}
if ($Modo -eq 'Categoria') {
    if ($PSBoundParameters.ContainsKey('Modulo') -or $PSBoundParameters.ContainsKey('UnidadFormativa')) {
        throw 'No mezcles categoria/subcategoria con modulo/unidad formativa.'
    }
    $interactiveCatalog = -not $PSBoundParameters.ContainsKey('Categoria') -or
        -not $PSBoundParameters.ContainsKey('Dificultad') -or
        -not $PSBoundParameters.ContainsKey('Cantidad')
    if ($interactiveCatalog) {
        Write-Host 'Catalogo: base de datos configurada en Django (no se consulta la URL base).'
        $catalog = Get-LocalKnowledgeCatalog
        $categories = @($catalog.categories)
        if ($categories.Count -eq 0) { throw 'No hay categorias activas en el catalogo.' }
        if (-not $PSBoundParameters.ContainsKey('Categoria')) {
            Write-Host 'Selecciona una categoria:'
            for ($i = 0; $i -lt $categories.Count; $i++) {
                Write-Host ("{0}. {1} (ID {2})" -f ($i + 1), $categories[$i].nombre, $categories[$i].id)
            }
            $index = Read-IntegerInRange -Label 'Categoria' -Minimum 1 -Maximum $categories.Count
            $Categoria = $categories[$index - 1].id
            $PSBoundParameters['Categoria'] = $Categoria
        }
        if (-not ($categories | Where-Object { $_.id -eq $Categoria })) {
            throw 'La categoria seleccionada no esta activa o no existe en el catalogo local.'
        }
        if (-not $PSBoundParameters.ContainsKey('Subcategoria')) {
            $subcategories = @($catalog.subcategories | Where-Object { $_.category_id -eq $Categoria })
            Write-Host 'Selecciona una subcategoria:'
            Write-Host '0. Todas las subcategorias'
            for ($i = 0; $i -lt $subcategories.Count; $i++) {
                Write-Host ("{0}. {1} (ID {2})" -f ($i + 1), $subcategories[$i].name, $subcategories[$i].id)
            }
            $index = Read-IntegerInRange -Label 'Subcategoria' -Minimum 0 -Maximum $subcategories.Count
            if ($index -gt 0) {
                $PSBoundParameters['Subcategoria'] = [string]$subcategories[$index - 1].id
            }
        }
    }
    $fields = [ordered]@{ Categoria = 'categoria' }
    if ($PSBoundParameters.ContainsKey('Subcategoria')) {
        $fields['Subcategoria'] = 'subcategoria'
    }
} else {
    if ($PSBoundParameters.ContainsKey('Categoria') -or $PSBoundParameters.ContainsKey('Subcategoria')) {
        throw 'No mezcles modulo/unidad formativa con categoria/subcategoria.'
    }
    $fields = [ordered]@{ Modulo = 'modulo'; UnidadFormativa = 'unidad_formativa' }
}

foreach ($field in $fields.Keys) {
    if ($PSBoundParameters.ContainsKey($field)) {
        $value = $PSBoundParameters[$field]
        if ($field -eq 'Subcategoria') {
            $value = $value.Trim()
            if (-not $value -or $value.Length -gt 120) {
                throw 'Subcategoria debe ser un nombre o ID, de 1 a 120 caracteres.'
            }
            if ($value -match '^[+-]?\d+$' -and [decimal]$value -le 0) {
                throw 'Subcategoria debe ser un ID positivo o un nombre.'
            }
        } elseif ($value -lt 1) { throw "$field debe ser un ID entero positivo." }
    } else {
        $value = Read-IntegerInRange -Label "ID $field" -Minimum 1 -Maximum ([int]::MaxValue)
    }
    $filters[$fields[$field]] = $value
}

foreach ($field in @(
    @{ Name = 'Dificultad'; Key = 'dificultad'; Maximum = 5 },
    @{ Name = 'Cantidad'; Key = 'numero_preguntas'; Maximum = 100 }
)) {
    if ($PSBoundParameters.ContainsKey($field.Name)) {
        $value = $PSBoundParameters[$field.Name]
        if ($value -lt 1 -or $value -gt $field.Maximum) {
            throw "$($field.Name) debe estar entre 1 y $($field.Maximum)."
        }
    } else {
        $value = Read-IntegerInRange -Label $field.Name -Minimum 1 -Maximum $field.Maximum
    }
    $filters[$field.Key] = $value
}

$query = ($filters.GetEnumerator() | ForEach-Object {
    '{0}={1}' -f [Uri]::EscapeDataString($_.Key), [Uri]::EscapeDataString([string]$_.Value)
}) -join '&'

$endpoint = $baseUri.AbsoluteUri.TrimEnd('/') + '/api/v1/knowledge/questions/'
Write-Output ($endpoint + '?' + $query)
