# homeassistant-grohe_sense
Grohe Sense integration for Home Assistant

This is an integration to get Grohe Sense (small leak sensor) and Grohe Sense Guard (main water pipe sensor/breaker) sensors into Home Assistant. Far from production quality, not affiliated with Grohe. My understanding of the protocol is based on https://github.com/FlorianSW/grohe-ondus-api-java.

When you install this, you get the following sensors for Sense:
 - **humidity**
 - **temperature**
 - **notifications**

It's a small, battery-powered device, so don't expect frequent updates. It seems to measure every hour, but the app also said it only uploads every 24h. The sensors I implemented only give the latest measurement returned from the server.
 
When you install this, you get the following sensors for each Sense Guard (subject to change, still haven't figured out what makes sense really):
 - **1_day** Liters of water withdrawn today (resets to 0 at midnight)
 - **7_day** Liters of water withdrawn during the last 144 hours.
 - **flowrate**
 - **pressure** 
 - **temperature_guard**
 - **notifications**

You will also get a switch device (so, be careful with `group.all_switches`, as that now includes your water) called
 - **valve**

The Sense Guard uploads data to its server every 15 minutes (at least the one I have), so don't expect to use this for anything close to real-time. For water withdrawals, it seems to report the withdrawal only when it ends, so if you continuously withdraw water, I guess those sensors may stay at 0. Hopefully, that would show up in the flowrate sensor.

The notifications sensor is a string of all your unread notifications (newline-separated). I recommend installing the Grohe Sense app, where there is a UI to read them (so they disappear from this sensor). On first start, you may find you have a lot of old unread notifications. The notifications I know how to parse are listed in `NOTIFICATION_TYPES` in `sensor.py`, if the API returns something unknown, it will be shown as `Unknown notification:` and then a json dump. If you see that, please consider submitting a bug report with the `category` and `type` fields from the Json + some description of what it means (can be found by finding the corresponding notification in the Grohe Sense app).

## Automation ideas
- Turning water off when you're away (and dishwasher, washer, et.c. are not running) and turning it back on when home again.
- Turning water off when non-Grohe sensors detect water.
- Passing along notifications from Grohe sense to Slack (note that there is a polling delay, plus unknown delay between device and Grohe's cloud)
- Send Slack notification when your alarm is armed away and flowrate is >0 (controlling for the high latency, plus dishwashers, ice makers, et.c.).

Graphing water consumption is also nice. Note that the data returned by Grohe's servers is extremely detailed, so for nicer graphs, you may want to talk to the servers directly and access the json data, rather than go via this integration.

## Installation
- Ensure everything is set up and working in Grohe's Ondus app
- Copy this folder to `<config_dir>/custom_components/grohe_sense/`

Put the following in your home assistant config (N.B., format has changed, this component is no longer configured as a sensor platform)
```
grohe_sense:
  username: "YOUR_GROHE_EMAIL"
  password: "YOUR_GROHE_PASSWORD"
```

## Remarks on the "API"
I have not seen any documentation from Grohe on the API this integration is using, so likely it was only intended for their app. Breaking changes have happened previously, and can easily happen again. I make no promises that I'll maintain this module when that happens.

The API returns _much_ more detailed data than is exposed via these sensors. For withdrawals, it returns an exact start- and endtime for each withdrawal, as well as volume withdrawn. It seems to store data since the water meter was installed, so you can extract a lot of historic data (but then polling gets a bit slow). I'm not aware of any good way to expose time series data like this in home assistant (suddenly I learn that 2 liters was withdrawn 5 minutes ago, and 5 liters was withdrawn 2 minutes ago). If anyone has any good ideas/pointers, that'd be appreciated.
