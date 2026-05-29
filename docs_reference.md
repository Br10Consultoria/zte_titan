# Referência ZTE Titan - Comandos e Estrutura

## Estrutura do Manual (52 páginas)
- Série TITAN – C600, C610, C620, C650
- Fabricante: Multilaser PRO

## Comandos de Consulta ONU (SHOW only)

### Status das ONUs
```
show gpon onu state gpon-olt_<SLOT>/<PON>
show gpon onu state gpon-onu_<SLOT>/<PON>:<ONU_ID>
```

### Detalhes da ONU
```
show gpon onu detail-info gpon-onu_<SLOT>/<PON>:<ONU_ID>
show gpon onu detail gpon-onu_<SLOT>/<PON>:<ONU_ID>
```

### Potência Óptica
```
show pon power attenuation gpon-onu_<SLOT>/<PON>:<ONU_ID>
show pon power olt-rx gpon-olt_<SLOT>/<PON>
show pon power olt-tx gpon-olt_<SLOT>/<PON>
```

### Distância
```
show gpon onu distance gpon-onu_<SLOT>/<PON>:<ONU_ID>
show gpon onu ranging gpon-onu_<SLOT>/<PON>:<ONU_ID>
```

### ONUs Provisionadas/Não Provisionadas
```
show gpon onu baseinfo gpon-olt_<SLOT>/<PON>
show gpon onu uncfg
```

### Temperatura
```
show gpon onu temperature gpon-onu_<SLOT>/<PON>:<ONU_ID>
```

### Firmware
```
show gpon onu firmware-version gpon-onu_<SLOT>/<PON>:<ONU_ID>
```

### Status WAN
```
show gpon remote-onu wan-info gpon-onu_<SLOT>/<PON>:<ONU_ID>
```

### Status VoIP
```
show gpon remote-onu voip-status gpon-onu_<SLOT>/<PON>:<ONU_ID>
```

### Comandos OLT
```
show software
show card
show fan
show power
show uptime
show interface gpon-olt_<SLOT>/<PON>
show gpon onu baseinfo gpon-olt_<SLOT>/<PON>
```

## Faixas de Sinal
- RX ONU: -8 a -27 dBm = Normal | -27 a -29 dBm = Atenção | < -29 dBm = Crítico
- RX OLT: -10 a -25 dBm = Normal | -25 a -28 dBm = Atenção | < -28 dBm = Crítico
- TX OLT: +4 a +6 dBm = Normal

## Status Operacional
- working = Online
- disable = Offline
- initial = Registrando
- ranging = Sincronizando distância
- standby = Aguardando ativação

## Last Down Cause
- DyingGasp = Falta de energia
- LOS = Perda de sinal óptico
- LOF = Perda de sincronismo GPON
- PowerOff = ONU desligada
- Reboot = Reinicialização
- OMCI Down = Falha OMCI
- Deactive = ONU removida/desautorizada
- OLT Reset = Reinicialização da OLT
