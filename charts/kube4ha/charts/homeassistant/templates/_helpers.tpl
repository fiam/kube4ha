{{/*
Expand the name of the chart.
*/}}
{{- define "homeassistant.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Name the CronJob that stages Zigbee2MQTT data for Home Assistant backups.
*/}}
{{- define "homeassistant.zigbee2mqttBackupName" -}}
{{- printf "%s-z2m-backup" (include "homeassistant.fullname" . | trunc 52 | trimSuffix "-") }}
{{- end }}

{{/*
Name the Deployment that runs the Matter controller beside Home Assistant.
*/}}
{{- define "homeassistant.matterName" -}}
{{- printf "%s-matter" (include "homeassistant.fullname" . | trunc 56 | trimSuffix "-") }}
{{- end }}

{{/*
Name a repository-local or remotely fetched custom component volume.
*/}}
{{- define "homeassistant.customComponentName" -}}
{{- $componentName := .name | replace "_" "-" }}
{{- $full := printf "%s-custom-%s" (include "homeassistant.fullname" .root) $componentName }}
{{- if le (len $full) 63 }}
{{- $full }}
{{- else }}
{{- printf "%s-cc-%s" (include "homeassistant.fullname" .root | trunc 45 | trimSuffix "-") (.name | sha256sum | trunc 12) }}
{{- end }}
{{- end }}

{{/*
Convert a Home Assistant integration domain into a Kubernetes-safe suffix.
Home Assistant domains use underscores, while Kubernetes names use hyphens.
Hyphens are rejected in the source domain, so this mapping is unambiguous.
*/}}
{{- define "homeassistant.customComponentKey" -}}
{{- . | replace "_" "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "homeassistant.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "homeassistant.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "homeassistant.labels" -}}
helm.sh/chart: {{ include "homeassistant.chart" . }}
{{ include "homeassistant.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "homeassistant.selectorLabels" -}}
app.kubernetes.io/name: {{ include "homeassistant.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "homeassistant.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "homeassistant.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
