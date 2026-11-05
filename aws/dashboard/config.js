// Lo reescribe scripts/aws_practica3.ps1 con la URL real de la API.
window.APP_CONFIG = {
  apiUrl: "https://REEMPLAZAR.execute-api.us-east-1.amazonaws.com",
  apiToken: "REEMPLAZAR",
  dispositivo: "esp32-dht11",
  rangos: { tMin: 18, tMax: 22, tAire: 25, hMin: 40, hMax: 60 },
  refrescoMs: 10000,
};
