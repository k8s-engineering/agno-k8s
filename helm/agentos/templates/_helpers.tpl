{{/*
Expand the name of the chart.
*/}}
{{- define "agentos.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "agentos.fullname" -}}
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
{{- define "agentos.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "agentos.labels" -}}
helm.sh/chart: {{ include "agentos.chart" . }}
{{ include "agentos.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "agentos.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agentos.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "agentos.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "agentos.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Build the standard PostgreSQL connection URL (SQLAlchemy + psycopg driver).
Format: postgresql+psycopg://{user}:{password}@{host}:{port}/{database}
The password is injected via K8s env var substitution $(DB_PASS).
*/}}
{{- define "agentos.postgresUrl" -}}
{{- printf "postgresql+psycopg://%s:$(DB_PASS)@%s:%s/%s" .Values.database.appUser (required "database.host is required" .Values.database.host) (toString .Values.database.port) .Values.database.name }}
{{- end }}

{{/*
Build raw PostgreSQL URL (no driver prefix, for tools that need plain postgres://).
*/}}
{{- define "agentos.postgresRawUrl" -}}
{{- printf "postgresql://%s:$(DB_PASS)@%s:%s/%s" .Values.database.appUser (required "database.host is required" .Values.database.host) (toString .Values.database.port) .Values.database.name }}
{{- end }}
