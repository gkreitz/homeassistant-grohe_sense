# homeassistant-grohe_sense
Grohe Sense integration for Home Assistant

This is an integration to get Grohe Sense (small leak sensor) and Grohe Sense Guard (main water pipe sensor/breaker) sensors into Home Assistant. Far from production quality, not affiliated with Grohe. My understanding of the protocol is based on https://github.com/FlorianSW/grohe-ondus-api-java.

When you install this, you get the following sensors for Sense:
 - **humidity**
 - **temperature**
It's a small, battery-powered device, so don't expect frequent updates. It seems to measure every hour, but the app also said it only uploads every 24h. The sensors I implemented only give the latest measurement returned from the server.
 
When you install this, you get the following sensors for each Sense Guard (subject to change, still haven't figured out what makes sense really):
 - **1_day** Liters of water withdrawn today (resets to 0 at midnight)
 - **7_day** Liters of water withdrawn during the last 144 hours.
 - **flowrate**
 - **pressure** 
 - **temperature_guard**
The Sense Guard uploads data to its server every 15 minutes (at least the one I have), so don't expect to use this for anything close to real-time. For water withdrawals, it seems to report the withdrawal only when it ends, so if you continuously withdraw water, I guess those sensors may stay at 0. Hopefully, that would show up in the flowrate sensor.

This integration currently only implements the above sensors. So, you can't do any actions (e.g., turn water on/off), and you don't get any alerts on events (I'd be interested in implementing this, pointers to any documentation on protocol for alerts would be much appreciated).

## Automation ideas
With the limitations above, it's not quite obvious what automations, if any, to set up. If I get around to implementing water on/off, turning it off when the alarm is armed away and no water using machines are on may be an idea. Another would be send off a notification when the alarm is armed away and flowrate is >0 (controlling for the high latency, plus dishwashers, ice makers, et.c.).

Graphing water consumption is also nice. Note that the data returned by Grohe's servers is extremely detailed, so for nicer graphs, you may want to talk to the servers directly and access the json data, rather than go via this integration.

## Installation
- Ensure everything is set up and working in Grohe's Ondus app
- Copy this folder to `<config_dir>/custom_components/grohe_sense/`
- Go to https://idp2-apigw.cloud.grohe.com/v3/iot/oidc/login
- Bring up developer tools
- Log in, that'll try redirecting your browser with a 302 to an url starting with `ondus://idp2-apigw.cloud.grohe.com/v3/iot/oidc/token`, which an off-the-shelf Chrome will ignore
- You should see this failed redirect in your developer tools. Copy out the full URL and replace `ondus` with `https` and visit that URL (will likely only work once, and will expire, so don't be too slow).
- This gives you a json response. Save it and extract refresh_token from it (manually, or `jq .refresh_token < file.json`)

Put the following in your home assistant config:
```
sensor:
 - platform: grohe_sense
   refresh_token: "YOUR_VERY_VERY_LONG_REFRESH_TOKEN"
```

## Remarks on the "API"
I have not seen any documentation from Grohe on the API this integration is using, so likely it was only intended for their app. Breaking changes have happened previously, and can easily happen again. I make no promises that I'll maintain this module when that happens.

The API returns _much_ more detailed data than is exposed via these sensors. For withdrawals, it returns an exact start- and endtime for each withdrawal, as well as volume withdrawn. It seems to store data since the water meter was installed, so you can extract a lot of historic data (but then polling gets a bit slow). I'm not aware of any good way to expose time series data like this in home assistant (suddenly I learn that 2 liters was withdrawn 5 minutes ago, and 5 liters was withdrawn 2 minutes ago). If anyone has any good ideas/pointers, that'd be appreciated.
