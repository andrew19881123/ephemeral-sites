{{/*
Common labels and name helpers for ephemeral-sites.
*/}}
{{- define "ephemeral-sites.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ephemeral-sites.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "ephemeral-sites.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ephemeral-sites.labels" -}}
helm.sh/chart: {{ include "ephemeral-sites.chart" . }}
app.kubernetes.io/name: {{ include "ephemeral-sites.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "ephemeral-sites.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ephemeral-sites.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
