{{- define "pulse.labels" -}}
app.kubernetes.io/part-of: pulse
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end }}

{{/* Selector labels for one component: include "pulse.selector" (list . "api") */}}
{{- define "pulse.selector" -}}
app.kubernetes.io/name: pulse-{{ index . 1 }}
app.kubernetes.io/instance: {{ (index . 0).Release.Name }}
{{- end }}

{{- define "pulse.image" -}}
{{ (index . 0).Values.image.registry }}/pulse-{{ index . 1 }}:{{ (index . 0).Values.image.tag }}
{{- end }}

{{/* Pod and container hardening shared by the Pulse services (images run as uid 1000) */}}
{{- define "pulse.podSecurity" -}}
runAsNonRoot: true
runAsUser: 1000
runAsGroup: 1000
seccompProfile:
  type: RuntimeDefault
{{- end }}

{{- define "pulse.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end }}

{{- define "pulse.env" -}}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Release.Name }}-postgres
      key: database-url
- name: NATS_URL
  value: nats://{{ .Release.Name }}-nats:4222
{{- end }}
