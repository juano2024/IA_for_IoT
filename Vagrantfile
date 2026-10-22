# -*- mode: ruby -*-
# vi: set ft=ruby :
#
# Servidor Ubuntu del laboratorio "IA for IoT".
#
#   vagrant up        -> crea la VM, instala Docker Engine y levanta el contenedor
#   vagrant ssh       -> entra a la VM
#   vagrant halt      -> apaga la VM
#   vagrant destroy   -> destruye la VM
#
# El contenedor publica la API en el puerto 8080 de la VM y Vagrant lo reenvia
# al puerto 8080 del host, de modo que la API queda disponible en
# http://127.0.0.1:8080 y tambien en http://<IP_LAN_DEL_HOST>:8080 para que
# la pueda alcanzar el ESP32 desde la red.

Vagrant.configure("2") do |config|
  config.vm.box = "ubuntu/jammy64"
  config.vm.hostname = "ia-for-iot-server"

  # API: el contenedor publica en el puerto 8080 de la VM y Vagrant lo reenvia
  # al 8080 del host, escuchando en todas las interfaces (para el ESP32).
  config.vm.network "forwarded_port", guest: 8080, host: 8080, host_ip: "0.0.0.0"
  # Vagrant ya reenvia el SSH del invitado al puerto 2222 del host por defecto.

  # Carpeta compartida con el proyecto. Se usa solo como origen: el provisioner
  # copia el codigo a /home/vagrant/app porque Docker no puede montar en un
  # contenedor un shared folder de VirtualBox (vboxsf) y SQLite se bloquea en el.
  config.vm.synced_folder ".", "/vagrant", type: "virtualbox"

  config.vm.provider "virtualbox" do |vb|
    vb.name = "ia_for_iot_server"
    vb.memory = 2048
    vb.cpus = 2
  end

  config.vm.provision "shell", path: "deploy/provision.sh", privileged: true
end
