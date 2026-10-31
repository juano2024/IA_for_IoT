#!/bin/sh
# Genera el archivo de contrasenas de Mosquitto y arranca el broker.
set -eu

PASSWD=/mosquitto/data/passwd

mkdir -p /mosquitto/data /mosquitto/log
chown -R mosquitto:mosquitto /mosquitto/data /mosquitto/log

if [ -n "${MQTT_USER:-}" ] && [ -n "${MQTT_PASSWORD:-}" ]; then
  mosquitto_passwd -b -c "$PASSWD" "$MQTT_USER" "$MQTT_PASSWORD"
  chown mosquitto:mosquitto "$PASSWD"
  chmod 600 "$PASSWD"
  echo "Usuario MQTT '$MQTT_USER' configurado"
else
  echo "ADVERTENCIA: MQTT_USER/MQTT_PASSWORD vacios; el broker no aceptara conexiones"
fi

exec mosquitto -c /mosquitto/config/mosquitto.conf
