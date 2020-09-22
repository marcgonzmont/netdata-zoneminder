#Description: zoneminder netdata python.d module 
#Author: Jose Chapa
#This code is licensed under MIT license (see LICENSE.txt for details)
#Zoneminder API: https://zoneminder.readthedocs.io/en/stable/api.html

import json, time, os.path
import requests
import jwt

from bases.FrameworkServices.SimpleService import SimpleService

update_every = 10

ORDER = [
    'camera_fps', 
    'camera_bandwidth',
    'events',
    'disk_usage',
]

CHARTS = {
    'camera_fps': {
        'options': [None, 'Capture FPS', 'FPS', 'capture_fps', 'camera_fps', 'line'],
        'lines': []
    },
    'camera_bandwidth': {
        'options': [None, 'Capture Bandwidth', 'kB/s', 'camera_bandwidth', 'zm_camera.bandwidth', 'stacked'],
        'lines': []
    },
    'events': {
        'options': [None, 'Events', 'count', 'events', 'zm_camera.events', 'stacked'],
        'lines': []
    },
    'disk_usage': {
        'options': [None, 'Disk Space', 'GB', 'disk_space', 'zm_camera.disk_space', 'area'],
        'lines': [
            ['zm_disk_space', 'used', 'absolute', None, 1073741824]
        ]
    },
}

def zm_generate_refresh_token(zoneminder_url, zm_user, zm_password, connection_timeout):
    try:    
        post_data=dict()
        post_data["user"] = zm_user
        post_data["pass"] = zm_password
        r = requests.post(zoneminder_url + '/api/host/login.json', data=post_data, timeout=connection_timeout)
        json_data = r.json()
        if all (k in json_data for k in ("access_token","refresh_token")):
            try: 
                token_file = open(os.path.expanduser("~/.zm_token.txt"),'w')
                token_file.write("{}|{}".format(json_data["access_token"], json_data["refresh_token"]))
                token_file.close()  
            except IOError:
                return ("<error>", "Error while writing .zm_token.txt file.")   
            return ("ok", "{}|{}".format(json_data["access_token"], json_data["refresh_token"]))
        return ("<error>", "Invalid api response when trying to generate new access and refresh tokens: " + r.text)
    except requests.exceptions.RequestException as e: 
        return ("<error>", e)

def zm_generate_access_token(zoneminder_url, refresh_token, connection_timeout):
    try:
        r = requests.post(zoneminder_url + '/api/host/login.json?token=' + refresh_token, timeout=connection_timeout)
        json_data = r.json()
        if ("access_token" in json_data):
            try:
                token_file = open(os.path.expanduser("~/.zm_token.txt"),'w')
                token_file.write("{}|{}".format(json_data["access_token"], refresh_token))
                token_file.close()
            except IOError:
                return ("<error>", "Error while writing .zm_token.txt file.") 
            return ("ok", json_data["access_token"])
        return ("<error>", "Invalid api response when trying to generate new access token: " + r.text)
    except requests.exceptions.RequestException as e: 
        return ("<error>", e)

class Service(SimpleService):
    def __init__(self, configuration=None, name=None):
        SimpleService.__init__(self, configuration=configuration, name=name)
        self.order = ORDER
        self.definitions = CHARTS
        self.zoneminder_url = self.configuration.get("zm_url", "http://127.0.0.1/zm")
        self.zoneminder_url = self.zoneminder_url.strip('/')
        self.zm_user = self.configuration.get("zm_user", "")
        self.zm_password = self.configuration.get("zm_pass", "")
        self.connection_timeout = self.configuration.get("timeout", 10)
        
    def check(self):
       return True

    def _get_data(self):
        data = dict()
        access_token = refresh_token = ''
        bool_login = True
        disk_space = 0

        #if user is not defined, then do not attempt to login 
        if not self.zm_user:
            bool_login = False

        #get access token from file or zoneminder api
        if bool_login:
            try:
                token_file = open(os.path.expanduser("~/.zm_token.txt"))
                access_token,refresh_token = token_file.read().split('|')
                token_file.close()
            except IOError:
                result,output = zm_generate_refresh_token(self.zoneminder_url, self.zm_user, self.zm_password, self.connection_timeout)
                if ("<error>" in result):
                    self.debug("error: " + output)
                    return None
                self.debug("new access and refresh tokens were generated...")
                access_token,refresh_token = output.split('|')
        
            #get jwt information                             
            jwt_access_data = jwt.decode(access_token, verify=False)
            jwt_refresh_data = jwt.decode(refresh_token, verify=False)
                
            #get new refresh token if it expires in less than 30 minutes
            if ( ( jwt_refresh_data['exp'] - time.time() ) < 1800 ):
                self.debug("generating new refresh token...")
                result,output = zm_generate_refresh_token(self.zoneminder_url, self.zm_user, self.zm_password, self.connection_timeout)
                if ("<error>" in result):
                    self.debug("error: " + output)
                    return None
                access_token,refresh_token = output.split('|')
                                  
            #get new access token if current token expires in less than 5 minutes
            if ( ( jwt_access_data['exp'] - time.time() ) < 300 ):
                result,output = zm_generate_access_token(self.zoneminder_url, refresh_token, self.connection_timeout)
                if ("<error>" in result):
                    self.debug("error: " + output)
                    return None
                access_token = output
        
        #get data from monitors api call
        try:
            r = requests.get(self.zoneminder_url + '/api/monitors.json?token=' + access_token)
            json_data = r.json()  
        except requests.exceptions.RequestException as e: 
            self.debug(e)
            return None

        if all (k in json_data for k in ("success","data")):        
            if (json_data['success'] == False and 'Token revoked' in json_data['data']['name']):
                self.debug("token was revoked, generating new tokens, will try to collect data in next run...")
                result,output = zm_generate_refresh_token(self.zoneminder_url, self.zm_user, self.zm_password, self.connection_timeout)
                if ("<error>" in result):
                    self.debug("error: " + output)
                return None
               
        if ("monitors" in json_data):
            for i, monitor in enumerate(json_data["monitors"]):    
                disk_space += float(monitor["Monitor"]["TotalEventDiskSpace"])           
                if (monitor["Monitor"]["Function"] == "None" or monitor["Monitor"]["Enabled"] == "0"):
                    continue
                if "zm_fps_" + monitor["Monitor"]["Id"] not in self.charts['camera_fps']:
                    self.charts['camera_fps'].add_dimension(["zm_fps_" + monitor["Monitor"]["Id"], monitor["Monitor"]["Name"], 'absolute'])
                data["zm_fps_" + monitor["Monitor"]["Id"]] = float(monitor["Monitor_Status"]["CaptureFPS"])
                if "zm_bandwidth_" + monitor["Monitor"]["Id"] not in self.charts['camera_bandwidth']:
                    self.charts['camera_bandwidth'].add_dimension(["zm_bandwidth_" + monitor["Monitor"]["Id"], monitor["Monitor"]["Name"], 'absolute', None, 1024])
                data["zm_bandwidth_" + monitor["Monitor"]["Id"]] = float(monitor["Monitor_Status"]["CaptureBandwidth"])
                if "zm_events_" + monitor["Monitor"]["Id"] not in self.charts['events']:
                    self.charts['events'].add_dimension(["zm_events_" + monitor["Monitor"]["Id"], monitor["Monitor"]["Name"], 'absolute'])
                data["zm_events_" + monitor["Monitor"]["Id"]] = float(monitor["Monitor"]["TotalEvents"])
        else:
            self.debug("Invalid zoneminder api response: " + r.text)
            return None

        data["zm_disk_space"] = disk_space

        return data