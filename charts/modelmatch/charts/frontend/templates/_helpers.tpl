{{/*
Helpers for the frontend subchart. All define names are namespaced with `frontend.`
so they never collide with the backend subchart in the same umbrella render.
*/}}

{{- define "frontend.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "frontend.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "frontend.selectorLabels" -}}
app.kubernetes.io/name: {{ include "frontend.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "frontend.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "frontend.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: modelmatch
app.kubernetes.io/component: frontend
{{- end -}}

{{/*
ECR registry host (P33b, Option B): derived from the umbrella's global.awsAccountId +
global.awsRegion so the account id lives in ONE place. `image.registry` is an explicit
per-subchart override (unset by default). `required` makes a render without the globals
fail loudly instead of producing a broken ".dkr.ecr..amazonaws.com" host.
*/}}
{{- define "frontend.registry" -}}
{{- if .Values.image.registry -}}
{{- .Values.image.registry -}}
{{- else -}}
{{- $account := (required "global.awsAccountId is required (set in the umbrella values)" .Values.global.awsAccountId | toString) -}}
{{- $region := (required "global.awsRegion is required (set in the umbrella values)" .Values.global.awsRegion | toString) -}}
{{- printf "%s.dkr.ecr.%s.amazonaws.com" $account $region -}}
{{- end -}}
{{- end -}}

{{/*
Fully-qualified image ref from the derived registry + per-subchart repo. By TAG by
default (AWS/ECR, CI bumps `tag`). When `image.digest` is set (E21 home profile: the
public GHCR copy, immutable), the ref is registry/repo@sha256:… and `tag` is only
documentation — a digest can never be silently re-pointed by a tag push.
*/}}
{{- define "frontend.image" -}}
{{- if .Values.image.digest -}}
{{- printf "%s/%s@%s" (include "frontend.registry" .) .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s/%s:%s" (include "frontend.registry" .) .Values.image.repository (.Values.image.tag | toString) -}}
{{- end -}}
{{- end -}}
