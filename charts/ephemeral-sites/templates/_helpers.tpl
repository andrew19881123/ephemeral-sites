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

{{/*
Resolve the effective ingress annotations for a given host slot.
Usage: {{ include "ephemeral-sites.ingressAnnotations" (dict "ctx" . "host" "api") }}
Precedence: ingress.<host>.annotations > ingress.annotations.
*/}}
{{- define "ephemeral-sites.ingressAnnotations" -}}
{{- $ctx := .ctx -}}
{{- $host := index ($ctx.Values.ingress | default dict) .host | default dict -}}
{{- $ann := $host.annotations | default $ctx.Values.ingress.annotations -}}
{{- toYaml $ann -}}
{{- end -}}

{{/*
Resolve the effective ingress.tls.enabled for a given host slot.
Usage: {{ include "ephemeral-sites.ingressTlsEnabled" (dict "ctx" . "host" "api") }}
Returns "true" or "false" as a string — consumers compare with eq.
Precedence: ingress.<host>.tls.enabled (if key present) > ingress.tls.enabled.
*/}}
{{- define "ephemeral-sites.ingressTlsEnabled" -}}
{{- $ctx := .ctx -}}
{{- $host := index ($ctx.Values.ingress | default dict) .host | default dict -}}
{{- $hostTls := $host.tls | default dict -}}
{{- if hasKey $hostTls "enabled" -}}
{{- $hostTls.enabled -}}
{{- else -}}
{{- $ctx.Values.ingress.tls.enabled -}}
{{- end -}}
{{- end -}}
