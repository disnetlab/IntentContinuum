import re
import requests
import json
import time
import matplotlib.pyplot as plt
from datetime import datetime, timedelta,timezone
import pandas as pd
from collections import defaultdict

from mpl_toolkits.mplot3d.art3d import line_2d_to_3d
from scipy.interpolate import make_interp_spline
from config import db_url_get_time
from config import k3s_config_file
from config import onos_ip_address
from config import Prometheus_ip_address
from kubernetes import client, config
import openai
import os
import logging

openai.api_key = "Your_key"

# Global variables
output_file = "time_factors.json"  # Changed to relative path like your working code
summary_output_file = "RT_SFC.json"
average_rt_file = "avg_RT.json"
timestamps = []
response_times = []
estimated_rts = []  # Initialize estimated_rts here to avoid unresolved reference
average_response_times = []
violation_estimated_rts = []  # List to store estimated RTs where violations occur
min_threshold_estimated_rts = []
intent = 3
min_threshold= 1
alpha = 0.02  # Smoothing factor for Estimated RT
detected_violation_timestamp = None
detected_min_threshold_timestamp = None
violation_handled_time = None
min_threshold_handled_time = None
violation_handled = False
min_threshold_handled = False

retries = 1  # Global variable for controlling the cycle

fig_size = (10, 6)
fig, ax = plt.subplots(figsize=fig_size)
window_size = 30




# Set up logging configuration
logging.basicConfig(
    filename='A-main_loop_logs-gpt-pod-age',  # Log to the file named "experiment_logs"
    level=logging.INFO,  # Log level: INFO (you can also use DEBUG for more detailed logging)
    format='%(asctime)s - %(levelname)s - %(message)s',  # Log format
    datefmt='%Y-%m-%d %H:%M:%S'  # Date format
)



# Get cluster info from k3s api
#################################################################
#################################################################
#################################################################
def collect_and_filter_kubernetes_data():
    try:
        # Specify path to K3s configuration file
        k3s_config_path = k3s_config_file

        # Check if the configuration file exists and is readable
        if not os.path.exists(k3s_config_path) or not os.access(k3s_config_path, os.R_OK):
            raise FileNotFoundError("K3s configuration file does not exist or is not readable.")

        # Load the K3s configuration file
        config.load_kube_config(config_file=k3s_config_path)

        # Create Kubernetes API clients
        core_v1_api = client.CoreV1Api()
        apps_v1_api = client.AppsV1Api()

        # Retrieve list of nodes, pods, and deployments
        node_list = core_v1_api.list_node()
        pod_list = core_v1_api.list_pod_for_all_namespaces(watch=False)
        deployment_list = apps_v1_api.list_deployment_for_all_namespaces(watch=False)

        #
        # Collect node details
        nodes_data = []
        filtered_nodes_data = []
        for node in node_list.items:
            node_info = {
                "name": node.metadata.name,
                "capacity": {
                    "cpu": node.status.capacity.get('cpu', 'Not set'),
                    "memory": node.status.capacity.get('memory', 'Not set')
                },
                "allocatable": {
                    "cpu": node.status.allocatable.get('cpu', 'Not set'),
                    "memory": node.status.allocatable.get('memory', 'Not set')
                },
                "status": node.status.conditions[-1].type if node.status.conditions else "Unknown",
                "roles": node.metadata.labels.get('kubernetes.io/role', 'None'),
                "age": node.metadata.creation_timestamp.strftime(
                    '%Y-%m-%d %H:%M:%S') if node.metadata.creation_timestamp else "Unknown",
                "version": node.status.node_info.kubelet_version if node.status.node_info else "Unknown",
                "internal_ip": next(
                    (addr.address for addr in node.status.addresses if addr.type == "InternalIP"),
                    "Not Available"
                ),
                "external_ip": next(
                    (addr.address for addr in node.status.addresses if addr.type == "ExternalIP"),
                    "Not Available"
                ),
                "os_image": node.status.node_info.os_image if node.status.node_info else "Unknown",
                "kernel_version": node.status.node_info.kernel_version if node.status.node_info else "Unknown",
                "architecture": node.status.node_info.architecture if node.status.node_info else "Unknown"
            }
            nodes_data.append(node_info)

            # Filtered node data for important file
            filtered_nodes_data.append({"name": node_info["name"], "internal_ip": node_info["internal_ip"]})

        # Collect pod details
        pods_data = []
        important_pods_data = []
        for pod in pod_list.items:
            pod_info = {
                "name": pod.metadata.name,
                "namespace": pod.metadata.namespace,  # Keep namespace in original data
                "node": pod.spec.node_name,
                "resources": {
                    "cpu_limit": pod.spec.containers[0].resources.limits.get('cpu', 'Not set')
                    if pod.spec.containers and pod.spec.containers[0].resources and pod.spec.containers[
                        0].resources.limits
                    else "Not set",
                    "memory_limit": pod.spec.containers[0].resources.limits.get('memory', 'Not set')
                    if pod.spec.containers and pod.spec.containers[0].resources and pod.spec.containers[
                        0].resources.limits
                    else "Not set"
                }
            }
            pods_data.append(pod_info)

            # Filter pods with names starting with "microservice"
            if pod.metadata.name.startswith("microservice"):
                important_pod_info = {key: value for key, value in pod_info.items() if key != "namespace"}
                important_pods_data.append(important_pod_info)

            #
            # Collect deployment details
            deployments_data = []
            important_deployments_data = []
            for deployment in deployment_list.items:
                deployment_info = {
                    "name": deployment.metadata.name,
                    "namespace": deployment.metadata.namespace,  # Keep namespace in original data
                    "replicas": deployment.spec.replicas
                }
                deployments_data.append(deployment_info)

                # Filter deployments with "microservice" in their name
                if "microservice" in deployment.metadata.name:
                    important_deployment_info = {key: value for key, value in deployment_info.items() if
                                                 key != "namespace"}
                    important_deployments_data.append(important_deployment_info)

            # Save original data to JSON
            kubernetes_data = {
                "Kubernetes": {
                    "nodes": nodes_data,
                    "pods": pods_data,
                    "deployments": deployments_data
                }
            }
            with open("k3s_cluster_info_original.json", "w") as file:
                json.dump(kubernetes_data, file, indent=4)
            print("All details saved to k3s_cluster_info_original.json")
            logging.info("All details saved to k3s_cluster_info_original.json")

            # Save important data to JSON
            important_data = {
                "Kubernetes": {
                    "nodes": filtered_nodes_data,
                    "pods": important_pods_data,
                    "deployments": important_deployments_data

            }
        }
        with open("k3s_cluster_info_important.json", "w") as file:
            json.dump(important_data, file, indent=4)
        print("Important details saved to k3s_cluster_info_important.json")
        logging.info("Important details saved to k3s_cluster_info_important.json")

        # Save filtered node data to JSON
        with open("k3s_important_node_ip.json", "w") as file:
            json.dump(filtered_nodes_data, file, indent=4)
        print("Filtered node IPs saved to k3s_important_node_ip.json")
        logging.info("Filtered node IPs saved to k3s_important_node_ip.json")

    except Exception as e:
        print(f"An error occurred: {e}")
        logging.error(f"An error occurred: {e}")

def generate_microservice_placement(my_input_file, my_output_file):

    try:
        # Read the JSON file

        with open(my_input_file, "r") as file:
            data = json.load(file)


        # Extract the pods information
        pods = data.get("Kubernetes", {}).get("pods", [])

        # Create a list of worker placements
        worker_sequence = []
        for pod in pods:
            if pod["name"].startswith("microservice"):
                worker_sequence.append(pod['node'])

        # Join the worker placements in the required format
        placement_string = " -> ".join(worker_sequence)

        # Write the output to a text file
        with open(my_output_file, "w") as file:
            file.write(placement_string)

        print(f"Worker placement saved to {my_output_file}")

    except Exception as e:
        print(f"An error occurred: {e}")

print(">>>>> 33333333333")
# File paths
my_input_file = "k3s_cluster_info_important.json"
print(">>>>> 444444444444")
my_output_file = "k3s_worker_placement.txt"
print(">>>>> 5555555555555")



# Get network info from onos api
#################################################################
#################################################################
#################################################################
def make_request(url):
    headers = {'Authorization': 'Basic b25vczpyb2Nrcw=='}
    response = requests.get(url, headers=headers)
    return response.json()


# get hosts info from Rest API
def get_hosts():
    url = f"http://{onos_ip_address}/onos/v1/hosts"
    parsed = make_request(url)
    # with open('hosts.json', 'w') as f:
    # json.dump(parsed, f, indent=4)
    # print('\n**************************************************\n')
    # print(f"\033[1;30;42m >>>>> Number of hosts, including mininet hosts: {len(parsed['hosts'])} \n")

    # Filter out hosts with MAC addresses starting with '00:00:00:00:00'
    filtered_hosts = [host for host in parsed['hosts'] if not host['mac'].startswith('00:00:00:00:00')]

    # Print the filtered connectivity information
    # print('\033[1;30;44m >>>>> Connectivity between switches and K8-hosts \n')
    # for host in filtered_hosts:
    # print(host['locations'][0], host['ipAddresses'][0])

    # Save the filtered hosts data to 'onos_hosts.json'
    with open('onos_hosts.json', 'w') as f:
        json.dump({'hosts': filtered_hosts}, f, indent=4)

    return parsed


# get switches connectivity info from Rest API
def get_switchLinks():
    url = f"http://{onos_ip_address}/onos/v1/topology/clusters/0/links"
    parsed = make_request(url)

    # print('\033[1;30;46m >>>>> Connectivity between switches')
    # for links in parsed['links']:
    # print(links)

    with open('onos_links.json', 'w') as f:
        json.dump(parsed, f, indent=4)
    return parsed


# New function to create 'onos_hosts_important.json'- without extra details which are not needed.
def create_important_hosts_json():
    url = f"http://{onos_ip_address}/onos/v1/hosts"
    parsed = make_request(url)

    # Filter out hosts with MAC addresses starting with '00:00:00:00:00'
    filtered_hosts = [host for host in parsed['hosts'] if not host['mac'].startswith('00:00:00:00:00')]

    # Create a new file 'onos_hosts_important.json' with only 'mac', 'ipAddresses', and 'locations' keys
    important_hosts = [{'mac': host['mac'], 'ipAddresses': host['ipAddresses'], 'locations': host['locations']} for host
                       in filtered_hosts]

    with open('onos_hosts_important.json', 'w') as f:
        json.dump({'hosts': important_hosts}, f, indent=4)


# New function to create 'onos_links_important.json' without 'type' and 'state'
#################################################################################
def create_important_links_json():
    url = f"http://{onos_ip_address}/onos/v1/topology/clusters/0/links"
    parsed = make_request(url)

    # Filter out 'type' and 'state' from each link
    important_links = [{'src': link['src'], 'dst': link['dst']} for link in parsed['links']]

    # Save the modified links data to 'onos_links_important.json'
    with open('onos_links_important.json', 'w') as f:
        json.dump({'links': important_links}, f, indent=4)

#get flows info from Rest API
################################################################
def get_flows():
    url = f"http://{onos_ip_address}/onos/v1/flows"
    parsed = make_request(url)

    # Print basic information about the flows
    print('\n**************************************************\n')
    print(f"\033[1;30;42m >>>>> Number of flows retrieved: {len(parsed['flows'])} \n")

    # Save the flows data to 'onos_flows.json'
    with open('onos_flows.json', 'w') as f:
        json.dump(parsed, f, indent=4)

    print('\033[1;30;44m >>>>> Flow details saved to "onos_flows.json" \n')

    #
    # Filter flows with appId 'org.onosproject.fwd' and extract required parameters
    important_flows = []
    for flow in parsed.get("flows", []):
        if flow.get("appId") == "org.onosproject.fwd":
            important_flow = {
                "appId": flow.get("appId"),
                "priority": flow.get("priority"),
                "deviceId": flow.get("deviceId"),
                "instructions": [
                    {
                        "type": instruction.get("type"),
                        "port": instruction.get("port")
                    }
                    for instruction in flow.get("treatment", {}).get("instructions", [])
                ],
                "selector": {
                    "criteria": [
                        {
                            "type": criterion.get("type"),
                            **({"port": criterion.get("port")} if "port" in criterion else {}),
                            **({"mac": criterion.get("mac")} if "mac" in criterion else {})
                        }
                        for criterion in flow.get("selector", {}).get("criteria", [])
                        if criterion.get("type") in {"IN_PORT", "ETH_DST", "ETH_SRC"}
                    ]
                }
            }
            important_flows.append(important_flow)

    # Save the important flows to 'onos_important_flows.json'
    with open('onos_important_flows.json', 'w') as f:
        json.dump({"flows": important_flows}, f, indent=4)

    print('\033[1;30;44m >>>>> Important flow details saved to "onos_important_flows.json" \n')
    return parsed

def generate_more_important_flows():
    # Load data from 'onos_important_flows.json'
    with open('onos_important_flows.json', 'r') as f:
        data = json.load(f)

    # Filter out 'appId' and 'priority' fields
    more_important_flows = []
    for flow in data.get("flows", []):
        simplified_flow = {
            "deviceId": flow.get("deviceId"),
            "instructions": flow.get("instructions"),
            "selector": flow.get("selector")
        }
        more_important_flows.append(simplified_flow)

    # Save the filtered flows to 'onos_more_important_flows.json'
    with open('onos_more_important_flows.json', 'w') as f:
        json.dump({"flows": more_important_flows}, f, indent=4)

    print('>>>> More important flow details saved to "onos_more_important_flows.json"')




# Get monitoring data from Prometheus api
#################################################################
#################################################################
#################################################################
def query_prometheus_for_violation_data():
    logging.info("======= calling prometheus api to collect monitoring data (start)=======")
    # Define the Prometheus server URL
    prometheus_url = f"http://{Prometheus_ip_address}/api/v1/"

    # Use the detected violation timestamp from process_response_times
    if detected_violation_timestamp is None:
        print("No violation detected, skipping Prometheus data collection.")
        logging.info("No violation detected, skipping Prometheus data collection.")
        return


    # Convert violation timestamp to Unix time
    violation_timestamp = int(datetime.strptime(detected_violation_timestamp, '%Y-%m-%d %H:%M:%S').timestamp())
    monitor_window_size = 30  # 30 requests per window
    monitor_interval = '10s'
    numeric_value_monitor_interval = int(monitor_interval[:-1])  # Convert '10s' to 10 (seconds)

    # Queries for CPU, Memory, and traffic
    queries = {
        'cpu': 'sflow_cpu_utilization',
        'mem': 'sflow_mem_utilization',
        'ifin': 'sflow_ifinutilization',
        'ifout': 'sflow_ifoututilization'

    }

    # Function to retrieve all unique ifname values for traffic queries
    def get_ifnames():
        query = 'sflow_ifoututilization'
        params = {
            'query': query,
            'start': violation_timestamp - 900,
            'end': violation_timestamp + 900,
            'step': monitor_interval
        }
        response = requests.get(f"{prometheus_url}query_range", params=params)
        if response.status_code == 200:
            data = response.json()
            if data['status'] == 'success':
                results = data['data']['result']
                ifnames = set(result['metric'].get('ifname') for result in results if 'ifname' in result['metric'])
                return list(ifnames)
        return []

    # Function to calculate window start times for queries
    def calculate_window_times(violation_timestamp, monitor_window_size):
        step_interval = numeric_value_monitor_interval  # seconds step interval
        window_duration = monitor_window_size * step_interval

        third_window_start = violation_timestamp - window_duration  # Start of the third window (containing violation)
        second_window_start = third_window_start - window_duration  # Start of the second window
        first_window_start = second_window_start - window_duration  # Start of the first window

        return first_window_start, second_window_start

    # Function to query Prometheus and save the data
    def query_and_save_averages(query_name, filename, avg_filename, ifnames=None):
        output = {}
        avg_output = {}

        if ifnames:
            for ifname in ifnames:
                query = queries[query_name] + f'{{ifname="{ifname}"}}'
                params = {
                    'query': query,
                    'start': violation_timestamp - 900,  # 15 minutes before violation
                    'end': violation_timestamp + 900,  # 15 minutes after violation
                    'step': monitor_interval
                }

                response = requests.get(f"{prometheus_url}query_range", params=params)

                if response.status_code == 200:
                    data = response.json()
                    if data['status'] == 'success':
                        results = data['data']['result']
                        for result in results:
                            host = result['metric'].get('host', ifname)
                            output[host] = []
                            avg_output[host] = []
                            values = result['values']

                            # Calculate averages every 20 values
                            for i in range(0, len(values), 20):
                                subset = values[i:i + 20]
                                avg_value = sum(float(v[1]) for v in subset) / len(subset)
                                avg_timestamp = datetime.utcfromtimestamp(subset[-1][0]).strftime('%Y-%m-%d %H:%M:%S')

                                avg_output[host].append({
                                    "timestamp": avg_timestamp,
                                    query_name + "_avg_utilization": avg_value
                                })

                            # Save original data
                            for value in values:
                                timestamp = datetime.utcfromtimestamp(value[0]).strftime('%Y-%m-%d %H:%M:%S')
                                metric_value = value[1]

                                # Use the host field as the key (fallback to "unknown" if missing)
                                output_key = result['metric'].get('host', 'unknown')  # Ensure 'host' is used as key
                                if output_key not in output:
                                    output[output_key] = []

                                # Append the data, including the entire 'metric' dictionary
                                output[output_key].append({
                                    "timestamp": timestamp,
                                    query_name + "_utilization": metric_value,
                                    "metrics": result['metric']  # Include all metadata from 'metric'
                                })

        else:
            query = queries[query_name]
            params = {
                'query': query,
                'start': violation_timestamp - 900,  # 15 minutes before violation
                'end': violation_timestamp + 900,  # 15 minutes after violation
                'step': monitor_interval
            }

            response = requests.get(f"{prometheus_url}query_range", params=params)

            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success':
                    results = data['data']['result']
                    for result in results:
                        host = result['metric'].get('host', 'unknown')
                        output[host] = []
                        avg_output[host] = []
                        values = result['values']

                        # Calculate averages every 20 values
                        for i in range(0, len(values), 20):
                            subset = values[i:i + 20]
                            avg_value = sum(float(v[1]) for v in subset) / len(subset)
                            avg_timestamp = datetime.utcfromtimestamp(subset[-1][0]).strftime('%Y-%m-%d %H:%M:%S')

                            avg_output[host].append({
                                "timestamp": avg_timestamp,
                                query_name + "_avg_utilization": avg_value
                            })

                        # Save original data
                        for value in values:
                            timestamp = datetime.utcfromtimestamp(value[0]).strftime('%Y-%m-%d %H:%M:%S')
                            metric_value = value[1]
                            output[host].append({
                                "timestamp": timestamp,
                                query_name + "_utilization": metric_value
                            })
            else:
                print(f"Error querying Prometheus API: {response.status_code}")
                logging.error(f"Error querying Prometheus API: {response.status_code}")
                return

        with open(filename, 'w') as json_file:
            json.dump(output, json_file, indent=4)

        with open(avg_filename, 'w') as json_file:
            json.dump(avg_output, json_file, indent=4)

        print(f"Data has been saved to {filename} and averages to {avg_filename}")
        logging.info(f"Data has been saved to {filename} and averages to {avg_filename}")

    # Collect and save data for three windows before the violation
    def before_violation(query_name, output_filename, ifnames=None):
        output = {}

        # Calculate start and end times for the first two windows before the violation
        first_window_start, second_window_start = calculate_window_times(violation_timestamp, monitor_window_size)
        end_time = violation_timestamp - (
                    monitor_window_size * numeric_value_monitor_interval)  # End at the start of the third window

        # For ifin and ifout queries, apply ifnames, otherwise proceed normally
        if query_name in ['ifin', 'ifout'] and ifnames:
            for ifname in ifnames:
                query = queries[query_name] + f'{{ifname="{ifname}"}}'
                params = {
                    'query': query,
                    'start': first_window_start,
                    'end': end_time,
                    'step': monitor_interval
                }

                response = requests.get(f"{prometheus_url}query_range", params=params)

                if response.status_code == 200:
                    data = response.json()
                    if data['status'] == 'success':
                        results = data['data']['result']
                        for result in results:
                            host = result['metric'].get('host', ifname)
                            values = result['values']

                            # Average the data from the first two windows
                            all_values = [float(v[1]) for v in values[:2 * monitor_window_size]]
                            avg_value = sum(all_values) / len(all_values)

                            # Save the single averaged value in the output
                            output[host] = {
                                "timestamp": detected_violation_timestamp,
                                f"{query_name}_avg_before_violation": avg_value
                            }
                else:
                    print(f"Error querying Prometheus API for {ifname}: {response.status_code}")
                    logging.error(f"Error querying Prometheus API for {ifname}: {response.status_code}")
        else:
            query = queries[query_name]
            params = {
                'query': query,
                'start': first_window_start,
                'end': end_time,
                'step': monitor_interval
            }

            response = requests.get(f"{prometheus_url}query_range", params=params)

            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success':
                    results = data['data']['result']
                    for result in results:
                        host = result['metric'].get('host', 'unknown')
                        values = result['values']

                        # Average the data from the first two windows
                        all_values = [float(v[1]) for v in values[:2 * monitor_window_size]]
                        avg_value = sum(all_values) / len(all_values)

                        # Save the single averaged value in the output
                        output[host] = {
                            "timestamp": detected_violation_timestamp,
                            f"{query_name}_avg_before_violation": avg_value
                        }
            else:
                print(f"Error querying Prometheus API: {response.status_code}")
                logging.error(f"Error querying Prometheus API: {response.status_code}")

        # Save the output data to a JSON file
        with open(output_filename, 'w') as json_file:
            json.dump(output, json_file, indent=4)

        print(f"Single averaged data has been saved to {output_filename}")
        logging.info(f"Single averaged data has been saved to {output_filename}")

    # Function to query Prometheus for data during the violation window
    def violation(query_name, output_filename, ifnames=None):
        output = {}

        # Define the window size and calculate the third window, which contains the violation
        step_interval = numeric_value_monitor_interval  # seconds step interval
        window_duration = monitor_window_size * step_interval

        # The third window starts immediately before the violation timestamp and lasts for one window duration
        third_window_start = violation_timestamp - window_duration
        third_window_end = violation_timestamp

        # For ifin and ifout queries, apply ifnames, otherwise proceed normally
        if query_name in ['ifin', 'ifout'] and ifnames:
            for ifname in ifnames:
                query = queries[query_name] + f'{{ifname="{ifname}"}}'
                params = {
                    'query': query,
                    'start': third_window_start,
                    'end': third_window_end,
                    'step': monitor_interval
                }

                response = requests.get(f"{prometheus_url}query_range", params=params)

                if response.status_code == 200:
                    data = response.json()
                    if data['status'] == 'success':
                        results = data['data']['result']
                        for result in results:
                            host = result['metric'].get('host', ifname)
                            values = result['values']

                            # Calculate the average value for this violation window
                            utilization_values = [float(v[1]) for v in values]
                            avg_utilization = sum(utilization_values) / len(
                                utilization_values) if utilization_values else 0

                            # Save the single averaged value in the output
                            output[host] = {
                                "timestamp": detected_violation_timestamp,
                                f"{query_name}_avg_utilization_violation": avg_utilization
                            }
                else:
                    print(f"Error querying Prometheus API for {ifname}: {response.status_code}")
                    logging.error(f"Error querying Prometheus API for {ifname}: {response.status_code}")
        else:
            query = queries[query_name]
            params = {
                'query': query,
                'start': third_window_start,
                'end': third_window_end,
                'step': monitor_interval
            }

            response = requests.get(f"{prometheus_url}query_range", params=params)

            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success':
                    results = data['data']['result']
                    for result in results:
                        host = result['metric'].get('host', 'unknown')
                        values = result['values']

                        # Calculate the average value for this violation window
                        utilization_values = [float(v[1]) for v in values]
                        avg_utilization = sum(utilization_values) / len(utilization_values) if utilization_values else 0

                        # Save the single averaged value in the output
                        output[host] = {
                            "timestamp": detected_violation_timestamp,
                            f"{query_name}_avg_utilization_violation": avg_utilization
                        }
            else:
                print(f"Error querying Prometheus API: {response.status_code}")
                logging.error(f"Error querying Prometheus API: {response.status_code}")

        # Save the output data to a JSON file
        with open(output_filename, 'w') as json_file:
            json.dump(output, json_file, indent=4)

        print(f"Averaged violation window data has been saved to {output_filename}")
        logging.info(f"Averaged violation window data has been saved to {output_filename}")

    # Collect and save data for two windows after the violation
    def after_violation(query_name, output_filename, ifnames=None):
        output = {}

        # Calculate start and end times for two windows after the violation
        step_interval = numeric_value_monitor_interval  # seconds step interval
        window_duration = monitor_window_size * step_interval

        first_window_start = violation_timestamp + window_duration
        second_window_start = first_window_start + window_duration
        end_time = second_window_start + window_duration

        # For ifin and ifout queries, apply ifnames, otherwise proceed normally
        if query_name in ['ifin', 'ifout'] and ifnames:
            for ifname in ifnames:
                query = queries[query_name] + f'{{ifname="{ifname}"}}'
                params = {
                    'query': query,
                    'start': first_window_start,
                    'end': end_time,
                    'step': monitor_interval
                }

                response = requests.get(f"{prometheus_url}query_range", params=params)

                if response.status_code == 200:
                    data = response.json()
                    if data['status'] == 'success':
                        results = data['data']['result']
                        for result in results:
                            host = result['metric'].get('host', ifname)
                            values = result['values']

                            # Average the data from the two windows after the violation
                            all_values = [float(v[1]) for v in values[:2 * monitor_window_size]]
                            avg_value = sum(all_values) / len(all_values)

                            # Save the single averaged value in the output
                            output[host] = {
                                "timestamp": detected_violation_timestamp,
                                f"{query_name}_avg_after_violation": avg_value
                            }
                else:
                    print(f"Error querying Prometheus API for {ifname}: {response.status_code}")
                    logging.error("Error querying Prometheus API for {ifname}: {response.status_code}")
        else:
            query = queries[query_name]
            params = {
                'query': query,
                'start': first_window_start,
                'end': end_time,
                'step': monitor_interval
            }

            response = requests.get(f"{prometheus_url}query_range", params=params)

            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success':
                    results = data['data']['result']
                    for result in results:
                        host = result['metric'].get('host', 'unknown')
                        values = result['values']

                        # Average the data from the two windows after the violation
                        all_values = [float(v[1]) for v in values[:2 * monitor_window_size]]
                        avg_value = sum(all_values) / len(all_values)

                        # Save the single averaged value in the output
                        output[host] = {
                            "timestamp": detected_violation_timestamp,
                            f"{query_name}_avg_after_violation": avg_value
                        }
            else:
                print(f"Error querying Prometheus API: {response.status_code}")
                logging.error(f"Error querying Prometheus API: {response.status_code}")

        # Save the output data to a JSON file
        with open(output_filename, 'w') as json_file:
            json.dump(output, json_file, indent=4)

        print(f"Data after violation has been saved to {output_filename}")
        logging.info(f"Data after violation has been saved to {output_filename}")

    # Combine the before and after violation data
    def combine_before_after_violation(before_file, after_file, output_file):
        with open(before_file, 'r') as bf:
            before_data = json.load(bf)

        with open(after_file, 'r') as af:
            after_data = json.load(af)

        combined_data = {}
        for key, before_values in before_data.items():
            combined_data[key] = {}
            if key in after_data:
                for field, value in before_values.items():
                    if field != "timestamp":
                        after_field = field.replace("_before", "_after")
                        if after_field in after_data[key]:
                            combined_data[key][field] = value
                            combined_data[key][after_field] = after_data[key].get(after_field)

        with open(output_file, 'w') as output:
            json.dump(combined_data, output, indent=4)
        print(f"Combined data has been saved to {output_file}")
        logging.info(f"Combined data has been saved to {output_file}")

    def combined_violation_and_previolation(before_filename, violation_filename, output_filename):
        # Helper function to load JSON data from a file
        def load_json_file(filename):
            with open(filename, 'r') as json_file:
                return json.load(json_file)

        # Helper function to save combined data to a JSON file
        def save_combined_data(combined_data, output_filename):
            with open(output_filename, 'w') as json_file:
                json.dump(combined_data, json_file, indent=4)
            print(f"Combined data has been saved to {output_filename}")
            logging.info(f"Combined data has been saved to {output_filename}")

        # Combine data from before violation and during the violation
        def combine_before_and_violation(before_filename, violation_filename):
            before_data = load_json_file(before_filename)
            violation_data = load_json_file(violation_filename)

            combined_data = {}

            # Combine the two datasets
            for host, before_values in before_data.items():
                combined_data[host] = {key: value for key, value in before_values.items() if
                                       key != "timestamp"}  # Exclude timestamp field
                if host in violation_data:
                    for field, value in violation_data[host].items():
                        if field != "timestamp":  # Exclude the timestamp field
                            combined_data[host][field.replace('_avg_utilization',
                                                              '_avg_utilization_violation')] = value  # Add the violation data

            return combined_data

        # Combine the data and save it
        combined_data = combine_before_and_violation(before_filename, violation_filename)
        save_combined_data(combined_data, output_filename)

    #>>>>>new function as octet-start
    def octet(output_in_filename, output_out_filename):


        output_in = {}
        output_out = {}

        # Define queries for inoctets and outoctets
        queries = {
            'inbyte': 'sflow_ifinoctets',  # "inbyte" for inoctets
            'outbyte': 'sflow_ifoutoctets'  # "outbyte" for outoctets
        }

        # Query Prometheus for octet metrics
        for query_type, query_name in queries.items():
            output = output_in if query_type == 'inbyte' else output_out

            query = query_name
            params = {
                'query': query,
                'start': violation_timestamp - 900,
                'end': violation_timestamp + 900,
                'step': monitor_interval
            }

            response = requests.get(f"{prometheus_url}query_range", params=params)

            if response.status_code == 200:
                data = response.json()
                if data['status'] == 'success':
                    results = data['data']['result']
                    for result in results:
                        host = result['metric'].get('host', 'unknown')
                        link = result['metric'].get('link', 'unknown')
                        ifname = result['metric'].get('ifname', 'unknown')
                        values = result['values']

                        # Save the relevant data in the output
                        for value in values:
                            timestamp = datetime.utcfromtimestamp(value[0]).strftime(
                                '%Y-%m-%d %H:%M:%S')  # Convert to UTC timestamp
                            metric_value = float(value[1])  # Convert value to float
                            output_key = f"{host}_{ifname}"
                            if output_key not in output:
                                output[output_key] = {
                                    "host": host,
                                    "ifname": ifname,
                                    "link": link,
                                    "data": []
                                }
                            output[output_key]["data"].append({
                                "timestamp": timestamp,
                                "value": metric_value  # Keep "value" for raw data
                            })
            else:
                print(f"Error querying Prometheus API: {response.status_code}")
                logging.error(f"Error querying Prometheus API: {response.status_code}")

        # Save the initial unfiltered data
        with open(output_in_filename, 'w') as json_file:
            json.dump(output_in, json_file, indent=4)

        with open(output_out_filename, 'w') as json_file:
            json.dump(output_out, json_file, indent=4)

        print(f"Octet data has been saved to {output_in_filename} and {output_out_filename}")
        logging.info(f"Octet data has been saved to {output_in_filename} and {output_out_filename}")


        # Function to filter and simplify data
        def filter_and_simplify(data, query_type):

            simplified_data = []
            for key, value in data.items():
                # Include only if "link" is not "unknown"
                if value.get("link", "unknown") != "unknown":
                    for entry in value["data"]:
                        # Debugging: Print entry to verify structure
                        #print(f"Processing entry: {entry}")
                        #logging.debug(f"Processing entry: {entry}")

                        simplified_data.append({
                            "timestamp": entry["timestamp"],
                            f"{query_type}_value": entry["value"],  # Map "value" to "inbyte_value" or "outbyte_value"
                            "host": value.get("host", "unknown"),
                            "ifname": value.get("ifname", "unknown")
                        })
            return simplified_data

        # Filter and simplify input and output data separately
        filtered_in_data = filter_and_simplify(output_in, "inbyte")
        filtered_out_data = filter_and_simplify(output_out, "outbyte")

        # Save the filtered data into separate files
        filtered_in_filename = "monitor_filtered_ifinbytes.json"
        filtered_out_filename = "monitor_filtered_ifoutbytes.json"

        with open(filtered_in_filename, 'w') as json_file:
            json.dump(filtered_in_data, json_file, indent=4)

        with open(filtered_out_filename, 'w') as json_file:
            json.dump(filtered_out_data, json_file, indent=4)

        print(f"Filtered and simplified in-byte data has been saved to {filtered_in_filename}")
        logging.info(f"Filtered and simplified in-byte data has been saved to {filtered_in_filename}")
        print(f"Filtered and simplified out-byte data has been saved to {filtered_out_filename}")
        logging.info(f"Filtered and simplified out-byte data has been saved to {filtered_out_filename}")


        #>>>>>>>>>>>>>>>>previolation
        def filter_previolation_octets(filtered_file, output_file, query_type):
            # Ensure the global detected_violation_timestamp is set
            global detected_violation_timestamp
            if detected_violation_timestamp is None:
                print("No violation detected. Skipping pre-violation data collection for octets.")
                logging.info("No violation detected. Skipping pre-violation data collection for octets.")
                return

            # Convert detected_violation_timestamp to Unix time
            violation_timestamp = int(datetime.strptime(detected_violation_timestamp, '%Y-%m-%d %H:%M:%S').timestamp())

            # Define start and end of the pre-violation window (10 minutes before violation)
            pre_violation_start = violation_timestamp - 600  # 10 minutes = 600 seconds
            pre_violation_end = violation_timestamp  # Ends at the violation timestamp

            # Load the filtered octet data
            with open(filtered_file, 'r') as json_file:
                data = json.load(json_file)

            # Filter data based on the pre-violation window
            filtered_data = []
            values_for_avg = []
            for entry in data:
                # Convert timestamp to Unix time for comparison
                timestamp = datetime.strptime(entry["timestamp"], '%Y-%m-%d %H:%M:%S').timestamp()
                if pre_violation_start <= timestamp < pre_violation_end:  # Filter within pre-violation window
                    filtered_data.append(entry)
                    values_for_avg.append(float(entry[f"{query_type}_value"]))

            # Calculate the average of the filtered values
            avg_value = sum(values_for_avg) / len(values_for_avg) if values_for_avg else 0.0

            # Add average value to the result as a summary field
            summary = {
                "summary": {
                    "timestamp": detected_violation_timestamp,
                    f"{query_type}_avg_before_violation": avg_value
                }
            }
            filtered_data.append(summary)

            # Save the filtered pre-violation data
            with open(output_file, 'w') as json_file:
                json.dump(filtered_data, json_file, indent=4)

            print(f"Filtered and averaged pre-violation data for {query_type} has been saved to {output_file}")
            logging.info(f"Filtered and averaged pre-violation data for {query_type} has been saved to {output_file}")

        # Call the function for inbyte and outbyte
        filter_previolation_octets('monitor_filtered_ifinbytes.json', 'monitor_before_violation_ifinbytes.json',
                                   'inbyte')
        filter_previolation_octets('monitor_filtered_ifoutbytes.json', 'monitor_before_violation_ifoutbytes.json',
                                   'outbyte')



        #>>>>> here to get the average for the previolation data
        # New calculate_ifname_avg function
        def calculate_ifname_avg(input_file, output_file, query_type):
            """
            Calculates the average of values grouped by 'ifname' from the input JSON file.
            """
            # New function code here
            with open(input_file, 'r') as file:
                data = json.load(file)

            # Dictionary to store totals and counts
            ifname_data = defaultdict(lambda: {'total': 0, 'count': 0})
            for entry in data:
                if "ifname" in entry and f"{query_type}_value" in entry:
                    ifname = entry["ifname"]
                    value = float(entry[f"{query_type}_value"])
                    ifname_data[ifname]['total'] += value
                    ifname_data[ifname]['count'] += 1

            # Calculate averages and save to output
            result = []
            for ifname, stats in ifname_data.items():
                avg_value = stats['total'] / stats['count'] if stats['count'] > 0 else 0
                result.append({
                    "ifname": ifname,
                    f"{query_type}_avg_before_violation": avg_value
                })

            with open(output_file, 'w') as file:
                json.dump(result, file, indent=4)

            print(f"Averages grouped by 'ifname' saved to {output_file}")

        # Call the new function to generate average files
        calculate_ifname_avg('monitor_before_violation_ifinbytes.json', 'monitor_avg_before_violation_ifinbytes.json',
                             'inbyte')
        calculate_ifname_avg('monitor_before_violation_ifoutbytes.json', 'monitor_avg_before_violation_ifoutbytes.json',
                             'outbyte')



        #>>>>>> calculate violation data here
        def filter_violation_octets(filtered_file, output_file, query_type):
            """
            Filters data within the violation window (violation timestamp to 5 minutes after)
            and calculates average values.
            """
            global detected_violation_timestamp
            if detected_violation_timestamp is None:
                print("No violation detected. Skipping violation data collection for octets.")
                logging.info("No violation detected. Skipping violation data collection for octets.")
                return

            # Convert detected_violation_timestamp to Unix time
            violation_timestamp = int(datetime.strptime(detected_violation_timestamp, '%Y-%m-%d %H:%M:%S').timestamp())

            # Define start and end of the violation window (violation + 5 minutes = 300 seconds)
            violation_start = violation_timestamp
            violation_end = violation_timestamp + 300  # 5 minutes after violation

            # Load the filtered octet data
            with open(filtered_file, 'r') as json_file:
                data = json.load(json_file)

            # Filter data based on the violation window
            filtered_data = []
            values_for_avg = []
            for entry in data:
                # Convert timestamp to Unix time for comparison
                timestamp = datetime.strptime(entry["timestamp"], '%Y-%m-%d %H:%M:%S').timestamp()
                if violation_start <= timestamp < violation_end:  # Filter within violation window
                    filtered_data.append(entry)
                    values_for_avg.append(float(entry[f"{query_type}_value"]))

            # Calculate the average of the filtered values
            avg_value = sum(values_for_avg) / len(values_for_avg) if values_for_avg else 0.0

            # Add average value to the result as a summary field
            summary = {
                "summary": {
                    "timestamp": detected_violation_timestamp,
                    f"{query_type}_avg_violation": avg_value
                }
            }
            filtered_data.append(summary)

            # Save the filtered violation data
            with open(output_file, 'w') as json_file:
                json.dump(filtered_data, json_file, indent=4)

            print(f"Filtered and averaged violation data for {query_type} has been saved to {output_file}")
            logging.info(f"Filtered and averaged violation data for {query_type} has been saved to {output_file}")

        # Call the function for inbyte and outbyte violation data
        filter_violation_octets('monitor_filtered_ifinbytes.json', 'monitor_violation_ifinbytes.json', 'inbyte')
        filter_violation_octets('monitor_filtered_ifoutbytes.json', 'monitor_violation_ifoutbytes.json', 'outbyte')


        #####>>>>get average from the above
        def calculate_ifname_avg(input_file, output_file, query_type):
            """
            Calculates the average of values grouped by 'ifname' from the input JSON file.
            """
            # New function code here
            with open(input_file, 'r') as file:
                data = json.load(file)

            # Dictionary to store totals and counts
            ifname_data = defaultdict(lambda: {'total': 0, 'count': 0})
            for entry in data:
                if "ifname" in entry and f"{query_type}_value" in entry:
                    ifname = entry["ifname"]
                    value = float(entry[f"{query_type}_value"])
                    ifname_data[ifname]['total'] += value
                    ifname_data[ifname]['count'] += 1

            # Calculate averages and save to output
            result = []
            for ifname, stats in ifname_data.items():
                avg_value = stats['total'] / stats['count'] if stats['count'] > 0 else 0
                result.append({
                    "ifname": ifname,
                    f"{query_type}_avg_violation": avg_value
                })

            with open(output_file, 'w') as file:
                json.dump(result, file, indent=4)

            print(f"Averages grouped by 'ifname' saved to {output_file}")

        # Call the new function to generate average files
        calculate_ifname_avg('monitor_violation_ifinbytes.json', 'monitor_avg_violation_ifinbytes.json',
                             'inbyte')
        calculate_ifname_avg('monitor_violation_ifoutbytes.json', 'monitor_avg_violation_ifoutbytes.json',
                             'outbyte')

        #>>>>> add new function here to create combined jsno files for each
        def combine_octet(previolation_file, violation_file, output_file, query_type):
            """
            Combines pre-violation and violation octet data into a single file, grouped by 'ifname'.
            """
            # Load pre-violation data
            with open(previolation_file, 'r') as pre_file:
                previolation_data = json.load(pre_file)

            # Load violation data
            with open(violation_file, 'r') as vio_file:
                violation_data = json.load(vio_file)

            # Map pre-violation and violation data to dictionaries using 'ifname' as the key
            previolation_dict = {entry["ifname"]: entry for entry in previolation_data}
            violation_dict = {entry["ifname"]: entry for entry in violation_data}

            # Combine data based on 'ifname'
            combined_data = []
            all_ifnames = set(previolation_dict.keys()).union(violation_dict.keys())

            for ifname in all_ifnames:
                combined_entry = {
                    "ifname": ifname,
                    f"{query_type}_avg_before_violation": previolation_dict.get(ifname, {}).get(
                        f"{query_type}_avg_before_violation", 0),
                    f"{query_type}_avg_violation": violation_dict.get(ifname, {}).get(f"{query_type}_avg_violation", 0)
                }
                combined_data.append(combined_entry)

            # Save the combined data to the output file
            with open(output_file, 'w') as output_json:
                json.dump(combined_data, output_json, indent=4)

            print(f"Combined octet data has been saved to {output_file}")
            logging.info(f"Combined octet data has been saved to {output_file}")

        # Call the function for inbyte and outbyte
        combine_octet('monitor_avg_before_violation_ifinbytes.json', 'monitor_avg_violation_ifinbytes.json',
                      'monitor_combined_ifinbytes.json', 'inbyte')

        combine_octet('monitor_avg_before_violation_ifoutbytes.json', 'monitor_avg_violation_ifoutbytes.json',
                      'monitor_combined_ifoutbytes.json', 'outbyte')



    # Call the new octet function without ifnames
    octet('monitor_ifinoctets.json', 'monitor_ifoutoctets.json')

    # >>>>>new function as octet-end


    # Retrieve interface names (ifnames)
    ifnames = get_ifnames()

    # Query and save CPU, Memory, and Network Traffic data
    query_and_save_averages('cpu', 'monitor_cpu_original.json', 'monitor_cpu_important.json')
    query_and_save_averages('mem', 'monitor_mem_original.json', 'monitor_mem_important.json')
    query_and_save_averages('ifin', 'monitor_switch_inTraffic_original.json', 'monitor_switch_inTraffic_important.json',
                            ifnames)



    query_and_save_averages('ifout', 'monitor_switch_outTraffic_original.json',
                            'monitor_switch_outTraffic_important.json', ifnames)

    # Collect data for three windows before violation
    before_violation('cpu', 'monitor_avg_before_violation_cpu.json')
    before_violation('mem', 'monitor_avg_before_violation_mem.json')
    before_violation('ifin', 'monitor_avg_before_violation_ifin.json', ifnames)
    before_violation('ifout', 'monitor_avg_before_violation_ifout.json', ifnames)

    # Collect data for the window containing the violation
    violation('cpu', 'monitor_violation_cpu.json')
    violation('mem', 'monitor_violation_mem.json')
    violation('ifin', 'monitor_violation_ifin.json', ifnames)
    violation('ifout', 'monitor_violation_ifout.json', ifnames)

    # Collect data for two windows after violation
    after_violation('cpu', 'monitor_avg_after_violation_cpu.json')
    after_violation('mem', 'monitor_avg_after_violation_mem.json')
    after_violation('ifin', 'monitor_avg_after_violation_ifin.json', ifnames)
    after_violation('ifout', 'monitor_avg_after_violation_ifout.json', ifnames)

    # Combine data before and after violation
    combine_before_after_violation('monitor_avg_before_violation_cpu.json', 'monitor_avg_after_violation_cpu.json',
                                   'monitor_combined_data_cpu.json')
    combine_before_after_violation('monitor_avg_before_violation_mem.json', 'monitor_avg_after_violation_mem.json',
                                   'monitor_combined_data_mem.json')
    combine_before_after_violation('monitor_avg_before_violation_ifin.json', 'monitor_avg_after_violation_ifin.json',
                                   'monitor_combined_data_ifin.json')
    combine_before_after_violation('monitor_avg_before_violation_ifout.json', 'monitor_avg_after_violation_ifout.json',
                                   'monitor_combined_data_ifout.json')

    # Call the function to combine the before and violation data
    combined_violation_and_previolation('monitor_avg_before_violation_cpu.json', 'monitor_violation_cpu.json',
                                        'monitor_combine_violation_and_previolation_cpu.json')
    combined_violation_and_previolation('monitor_avg_before_violation_mem.json', 'monitor_violation_mem.json',
                                        'monitor_combine_violation_and_previolation_mem.json')
    combined_violation_and_previolation('monitor_avg_before_violation_ifin.json', 'monitor_violation_ifin.json',
                                        'monitor_combine_violation_and_previolation_ifin.json')
    combined_violation_and_previolation('monitor_avg_before_violation_ifout.json', 'monitor_violation_ifout.json',
                                        'monitor_combine_violation_and_previolation_ifout.json')
    logging.info("======= calling prometheus api to collect monitoring data (end)=======")



    #>>>>utilization function
    def process_switch_traffic_data(input_in_file, input_out_file, output_in_file, output_out_file, avg_in_file,
                                    avg_out_file):
        """
        Reads and filters data from input JSON files, calculates averages for identical ifnames,
        and saves the output to new JSON files.

        Parameters:
            input_in_file (str): Input file for in-traffic data.
            input_out_file (str): Input file for out-traffic data.
            output_in_file (str): Output file for filtered in-traffic data.
            output_out_file (str): Output file for filtered out-traffic data.
            avg_in_file (str): Output file for average in-traffic utilization.
            avg_out_file (str): Output file for average out-traffic utilization.
        """

        def filter_data(input_file, query_type):
            """
            Filters data to include only entries with a link and extracts relevant fields.

            Parameters:
                input_file (str): The input JSON file.
                query_type (str): Type of data ('ifin_utilization' or 'ifout_utilization').

            Returns:
                list: Filtered and formatted data.
            """
            with open(input_file, 'r') as file:
                data = json.load(file)

            filtered_data = []
            for host, entries in data.items():
                for entry in entries:
                    if 'link' in entry['metrics'] and entry['metrics']['link'] != "unknown":
                        filtered_data.append({
                            "timestamp": entry["timestamp"],
                            "ifname": entry["metrics"]["ifname"],
                            "link": entry["metrics"]["link"],
                            query_type: entry[query_type]
                        })
            return filtered_data

        #
        def calculate_average_utilization(filtered_data, query_type):
            """
            Calculates the average utilization for each ifname.

            Parameters:
                filtered_data (list): List of filtered data.
                query_type (str): Type of data ('ifin_utilization' or 'ifout_utilization').

            Returns:
                list: Averaged utilization data.
            """
            utilization_data = {}

            for entry in filtered_data:
                ifname = entry["ifname"]
                link = entry["link"]
                utilization = float(entry[query_type])

                if ifname not in utilization_data:
                    utilization_data[ifname] = {"link": link, "total": 0, "count": 0}

                utilization_data[ifname]["total"] += utilization
                utilization_data[ifname]["count"] += 1

            averaged_data = []
            for ifname, data in utilization_data.items():
                avg_utilization = data["total"] / data["count"]
                averaged_data.append({
                    "ifname": ifname,
                    "link": data["link"],
                    f"{query_type}_avg_utilization": avg_utilization
                })

            return averaged_data


        ###
        # Process in-traffic data
        in_traffic_data = filter_data(input_in_file, "ifin_utilization")
        with open(output_in_file, 'w') as file:
            json.dump(in_traffic_data, file, indent=4)

        print(f"Filtered in-traffic data has been saved to {output_in_file}")
        logging.info(f"Filtered in-traffic data has been saved to {output_in_file}")

        # Calculate and save average in-traffic utilization
        avg_in_traffic_data = calculate_average_utilization(in_traffic_data, "ifin_utilization")
        with open(avg_in_file, 'w') as file:
            json.dump(avg_in_traffic_data, file, indent=4)

        print(f"Averaged in-traffic utilization has been saved to {avg_in_file}")
        logging.info(f"Averaged in-traffic utilization has been saved to {avg_in_file}")

        # Process out-traffic data
        out_traffic_data = filter_data(input_out_file, "ifout_utilization")
        with open(output_out_file, 'w') as file:
            json.dump(out_traffic_data, file, indent=4)

        print(f"Filtered out-traffic data has been saved to {output_out_file}")
        logging.info(f"Filtered out-traffic data has been saved to {output_out_file}")

        # Calculate and save average out-traffic utilization
        avg_out_traffic_data = calculate_average_utilization(out_traffic_data, "ifout_utilization")
        with open(avg_out_file, 'w') as file:
            json.dump(avg_out_traffic_data, file, indent=4)

        print(f"Averaged out-traffic utilization has been saved to {avg_out_file}")
        logging.info(f"Averaged out-traffic utilization has been saved to {avg_out_file}")

    # Define the input and output file paths
    input_in_file = "monitor_switch_inTraffic_original.json"
    input_out_file = "monitor_switch_outTraffic_original.json"
    output_in_file = "monitor_switch_in_utilization.json"
    output_out_file = "monitor_switch_out_utilization.json"
    avg_in_file = "monitor_switch_avg_in_utilization.json"
    avg_out_file = "monitor_switch_avg_out_utilization.json"

    # Call the function to process the data
    process_switch_traffic_data(input_in_file, input_out_file, output_in_file, output_out_file, avg_in_file,
                                avg_out_file)


# Building prompt for GPT
#################################################################
#################################################################
#################################################################
def build_prompt(index, average_response_time, story_details, combined_data):
    with open("detailed_prompt-flow.txt", "r") as prompt_file:
        detailed_prompt = prompt_file.read()

    with open('k3s_worker_placement.txt', 'r') as prompt_file:
        microservice_placement = prompt_file.read()




    ###### complete prompt before sending to api
    print('\033[94m ############## here is the build prompt function (start), showing the complete prompt sent to GPT ############## \033[0m')
    logging.info(print(
        '############## here is the build prompt function (start), showing the complete prompt sent to GPT #############'))
    prompt = (
        "I have a problem and I need you to solve it completely."
        f"{story_details}\n\n"
        f"the intent is defined as {intent} seconds, and the minimum threshold is {min_threshold} seconds."
        "Here is the data collected from the environment:\n"
        f"{combined_data}\n\n"
        f"what is the best shortest path to send the traffic between {microservice_placement}\n\n"
        "Please provide your response in a single complete JSON format containing the new flows.\n\n"
        f"{detailed_prompt}\n\n"

    )

    #print(prompt)
    logging.info(prompt)
    #print('\033[94m ##############  here is the build_prompt function (end), showing the complete prompt sent to GPT ############## \033[0m')
    logging.info("##############  here is the build_prompt function (end), showing the complete prompt sent to GPT ##############")
    return prompt

############################# NEW CONDITION (START) #############################
def build_prompt_new_condition(index, average_response_time, story_details, combined_data):
    with open("detailed_prompt_new_condition.txt", "r") as prompt_file:
        detailed_prompt = prompt_file.read()



    print(
        '\033[94m ############## here is the build_prompt_new_condition prompt function (start), showing the complete prompt sent to GPT ############## \033[0m')
    logging.info(print(
        '############## here is the build build_prompt_new_condition function (start), showing the complete prompt sent to GPT #############'))
    prompt = (
        "I have a problem and I need you to solve it completely."
        f"{story_details}\n\n"
        f"the intent is defined as {intent} seconds, and the minimum threshold is {min_threshold} seconds."
        "Here is the data collected from the environment:\n"
        f"{combined_data}\n\n"
        f"Number of requests: {index}."
        f"What could be the potential root cause and what actions should be taken to mitigate this issue?\n\n"
        "Please provide your response in a single complete JSON format containing all recommended actions.\n\n"
        f"{detailed_prompt}\n\n"

    )

    #print(prompt)
    logging.info(prompt)
    #print('\033[94m ##############  here is the build_prompt_new_condition function (end), showing the complete prompt sent to GPT ############## \033[0m')
    logging.info(
        "##############  here is the build_prompt_new_condition function (end), showing the complete prompt sent to GPT ##############")
    return prompt
############################# NEW CONDITION (END) #############################





# Interact with GPT
#################################################################
#################################################################
#################################################################
def interact_with_chatgpt(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-2024-08-06",
            messages=[
                {"role": "system", "content": "You are a helpful assistant."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1000,  # Increase the token limit
            n=1,
            stop=None,
            temperature=0.7,
        )
        # print('<<<<<<<<<<<<<<<< here is the interact_with_chatgpt function (start) >>>>>>>>>>>>>>>')
        # print(response)
        # print('<<<<<<<<<<<<<<<< here is the interact_with_chatgpt function (end) >>>>>>>>>>>>>>>')
        message = response['choices'][0]['message']['content'].strip()
        return message
    except Exception as e:
        print(f"XXX An error occurred while interacting with ChatGPT: {e}")
        logging.error(f"XXX An error occurred while interacting with ChatGPT: {e}")
        return None


# Error handling parts
#################################################################
#################################################################
#################################################################
def clean_json_response(response_str):
    # print("Raw JSON Response:")
    # print(response_str)  # Print the raw JSON for debugging

    # Remove comments and unnecessary characters
    response_str = re.sub(r'//.*', '', response_str)
    response_str = re.sub(r'/\*.*?\*/', '', response_str, flags=re.DOTALL)

    # Attempt to fix common JSON issues
    response_str = response_str.strip().strip('```json').strip('```').strip()

    # print("Cleaned JSON Response:")
    # print(response_str)  # Print the cleaned JSON for debugging

    return response_str


def validate_and_extract_json(json_str):
    try:
        response_json = json.loads(json_str)
        print("Parsed JSON:")
        print(json.dumps(response_json, indent=2))  # Pretty-print the parsed JSON for debugging
        return response_json
    except json.JSONDecodeError as e:
        print(f"Error decoding JSON response: {e}")
        logging.error(f"Error decoding JSON response: {e}")
        return None


def get_deployment_name_from_pod_name(pod_name):
    # Print the original pod name to debug the input
    print(f"Original pod name from GPT: {pod_name}")
    logging.info(f"Original pod name from GPT: {pod_name}")

    # Split the pod name by '-' and take the first two parts
    deployment_name = "-".join(pod_name.split("-")[:2])  # Take the first two parts

    # Print the extracted deployment name for debugging
    print(f"Extracted deployment name after splitting: {deployment_name}")
    logging.info(f"Extracted deployment name after splitting: {deployment_name}")
    return deployment_name


def align_response_with_deployments(response_json):
    reasons = response_json.get("Source of Violation", {}).get("Reason", [])
    for reason in reasons:
        description = reason.get("description", "")
        details = reason.get("details", {})
        if "high_cpu_utilization_on_pods" in description or "high_mem_utilization_on_pods" in description:
            pod_names = details.get("pods", [])
            deployment_names = [get_deployment_name_from_pod_name(pod_name) for pod_name in pod_names]
            details["pods"] = deployment_names

    recommended_action = response_json.get("Recommended Action", {})
    if recommended_action:
        if "name" in recommended_action:
            recommended_action["name"] = get_deployment_name_from_pod_name(recommended_action["name"])
        if "new_placement" in recommended_action:
            for placement in recommended_action["new_placement"]:
                pod_name = placement.get("pod")
                if pod_name:
                    placement["pod"] = get_deployment_name_from_pod_name(pod_name)
    return response_json


def handle_incomplete_json(response_str):
    try:
        # Attempt to load the JSON directly
        return json.loads(response_str)
    except json.JSONDecodeError:
        # Handle incomplete JSON, such as truncation
        last_bracket_index = response_str.rfind('}')
        if last_bracket_index != -1:
            response_str = response_str[:last_bracket_index + 1]
            try:
                return json.loads(response_str)
            except json.JSONDecodeError:
                return None
        return None


# Pod replacement function
#################################################################
#################################################################
#################################################################
def update_deployment_node_selector(json_input, retry_attempts=3):
    k3s_config_path = k3s_config_file
    if os.path.exists(k3s_config_path) and os.access(k3s_config_path, os.R_OK):
        config.load_kube_config(config_file=k3s_config_path)

        api_instance = client.AppsV1Api()

        for attempt in range(retry_attempts):

            try:
                # parse json input
                input_data = json.loads(json_input)
                deployment_name = input_data['deployment_name']
                deployment_namespace = input_data['deployment_namespace']
                node_selector = input_data['node_selector']

                # Call pod lifespan before making changes
                print(f"Fetching pod lifespans before updating the placement for {deployment_name}.")
                logging.info(f"Fetching pod lifespans before updating the placement for {deployment_name}.")
                get_pod_lifespans()  # Lifespan before the change



                print(
                    f"Attempting for pod replacement in deployment: {deployment_name} in namespace: {deployment_namespace}")
                logging.info(
                    f"Attempting for pod replacement in deployment: {deployment_name} in namespace: {deployment_namespace}")

                deployment = api_instance.read_namespaced_deployment(name=deployment_name,
                                                                     namespace=deployment_namespace)
                deployment.spec.template.spec.node_selector = node_selector

                api_instance.replace_namespaced_deployment(name=deployment_name, namespace=deployment_namespace,
                                                           body=deployment)
                print(
                    f"######### Node selector (for pod replacement) updated for Deployment {deployment_name} in namespace {deployment_namespace}")
                logging.info(
                    f"######### Node selector (for pod replacement) updated for Deployment {deployment_name} in namespace {deployment_namespace}")

                # Call pod lifespan before making changes
                print(f"Fetching pod lifespans after updating the placement for {deployment_name}.")
                logging.info(f"Fetching pod lifespans after updating the placement for {deployment_name}.")
                get_pod_lifespans()  # Lifespan before the change


                break
            except client.exceptions.ApiException as e:
                if e.status == 409 and attempt < retry_attempts - 1:
                    print(f"Conflict error while replacing the pod, retrying... (attempt {attempt + 1})")
                    logging.error(f"Conflict error while replacing the pod, retrying... (attempt {attempt + 1})")
                else:
                    print(f"Exception when updating pod replacement: {e}")
                    logging.error(f"Exception when updating pod replacement: {e}")
                    break
            except KeyError as e:
                print(f"Missing key in JSON input: {e}")
                logging.error(f"Missing key in JSON input: {e}")
                break
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON input: {e}")
                logging.error(f"Error decoding JSON input: {e}")
                break
    else:
        print("K3s configuration file does not exist or insufficient permissions to read the configuration file.")
        logging.error(
            "K3s configuration file does not exist or insufficient permissions to read the configuration file.")


# update cpu/mem limit for pods function
#################################################################
#################################################################
#################################################################
def update_deployment_resources(json_input):
    print(f"JSON Input: {json_input}")

    k3s_config_path = k3s_config_file
    if os.path.exists(k3s_config_path) and os.access(k3s_config_path, os.R_OK):
        config.load_kube_config(config_file=k3s_config_path)
        apps_v1_api = client.AppsV1Api()

        try:
            # parse json input
            input_data = json.loads(json_input)
            deployment_name = input_data['deployment_name']
            namespace = input_data['namespace']
            cpu_limit = input_data['new_cpu_limit']
            memory_limit = input_data['new_memory_limit']

            # Call pod lifespan before making changes
            print(f"Fetching pod lifespans before updating resources for {deployment_name}.")
            logging.info(f"Fetching pod lifespans before updating resources for {deployment_name}.")
            get_pod_lifespans()  # Lifespan before the change




            deployment = apps_v1_api.read_namespaced_deployment(deployment_name, namespace)
            print(f"Current resources for deployment {deployment_name}:")
            logging.info(f"Current resources for deployment {deployment_name}:")

            for container in deployment.spec.template.spec.containers:
                current_cpu_limit = container.resources.limits.get('cpu',
                                                                   'Not set') if container.resources.limits else 'Not set'
                current_memory_limit = container.resources.limits.get('memory',
                                                                      'Not set') if container.resources.limits else 'Not set'
                print(
                    f"  Container {container.name}: CPU Limit: {current_cpu_limit}, Memory Limit: {current_memory_limit}")
                logging.info(
                    f"  Container {container.name}: CPU Limit: {current_cpu_limit}, Memory Limit: {current_memory_limit}")

                if not container.resources.limits:
                    container.resources.limits = {}
                if not container.resources.requests:
                    container.resources.requests = {}
                container.resources.limits['cpu'] = cpu_limit
                container.resources.limits['memory'] = memory_limit
                container.resources.requests['cpu'] = cpu_limit
                container.resources.requests['memory'] = memory_limit

            apps_v1_api.patch_namespaced_deployment(deployment_name, namespace, deployment)
            print(
                f"#########Successfully updated resources for {deployment_name} to CPU Limit: {cpu_limit}, Memory Limit: {memory_limit}.")
            logging.info(
                f"#########Successfully updated resources for {deployment_name} to CPU Limit: {cpu_limit}, Memory Limit: {memory_limit}.")

            # Call pod lifespan before making changes
            print(f"Fetching pod lifespans after updating resources for {deployment_name}.")
            logging.info(f"Fetching pod lifespans after updating resources for {deployment_name}.")
            get_pod_lifespans()  # Lifespan before the change




        except client.exceptions.ApiException as e:
            print(f"Exception when updating deployment resources: {e}")
            logging.error(f"Exception when updating deployment resources: {e}")
        except KeyError as e:
            print(f"Missing key in JSON input: {e}")
            logging.error(f"Missing key in JSON input: {e}")
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON input: {e}")
            logging.error(f"Error decoding JSON input: {e}")
    else:
        print("K3s configuration file does not exist or insufficient permissions to read the configuration file.")
        logging.info(
            "K3s configuration file does not exist or insufficient permissions to read the configuration file.")




# Scale up replicas function
#################################################################
#################################################################
#################################################################
def update_deployment_replicas(json_input):
    k3s_config_path = k3s_config_file
    if os.path.exists(k3s_config_path) and os.access(k3s_config_path, os.R_OK):
        config.load_kube_config(config_file=k3s_config_path)
        apps_v1_api = client.AppsV1Api()
        try:
            input_data = json.loads(json_input)
            deployment_name = input_data['deployment_name']
            namespace = input_data['namespace']
            replicas = int(input_data['new_replicas'])


            #
            # Call pod lifespan before making changes
            print(f"Fetching pod lifespans before updating replicas for {deployment_name}.")
            logging.info(f"Fetching pod lifespans before updating replicas for {deployment_name}.")
            get_pod_lifespans()  # Lifespan before the change




            deployment = apps_v1_api.read_namespaced_deployment(deployment_name, namespace)
            current_replicas = deployment.spec.replicas
            print(f"Current replicas for deployment {deployment_name}: {current_replicas}")
            logging.info(f"Current replicas for deployment {deployment_name}: {current_replicas}")

            body = {
                "spec": {
                    "replicas": replicas
                }
            }
            apps_v1_api.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=body)
            print(f"#########Successfully updated {deployment_name} to have {replicas} replicas.")
            logging.info(f"#########Successfully updated {deployment_name} to have {replicas} replicas.")


            #
            # Call pod lifespan after making changes
            print(f"Fetching pod lifespans after updating replicas for {deployment_name}.")
            logging.info(f"Fetching pod lifespans after updating replicas for {deployment_name}.")
            get_pod_lifespans()  # Lifespan after the change



        except client.exceptions.ApiException as e:
            print(f"Exception when updating deployment replicas: {e}")
            logging.error(f"Exception when updating deployment replicas: {e}")
        except KeyError as e:
            print(f"Missing key in JSON input: {e}")
            logging.error(f"Missing key in JSON input: {e}")
        except json.JSONDecodeError as e:
            print(f"Error decoding JSON input: {e}")
            logging.error(f"Error decoding JSON input: {e}")
    else:
        print("K3s configuration file does not exist or insufficient permissions to read the configuration file.")
        logging.info(
            "K3s configuration file does not exist or insufficient permissions to read the configuration file.")



# get pod lifespan
#################################################################
#################################################################
#################################################################
def get_pod_lifespans():
    # Specify path to K3s configuration file
    k3s_config_path = k3s_config_file  # Replace with your actual config file path

    # Check if the configuration file exists and is readable
    if os.path.exists(k3s_config_path):
        if os.access(k3s_config_path, os.R_OK):
            print("Loading Kubernetes config...")
            config.load_kube_config(config_file=k3s_config_path)

            # Create Kubernetes API clients
            core_v1_api = client.CoreV1Api()

            ###
            try:
                # Fetch the latest list of pods
                print("Fetching pod information...")
                pod_list = core_v1_api.list_pod_for_all_namespaces(watch=False)

                # Read existing data from lifespans.txt
                existing_lifespans = {}
                if os.path.exists("lifespans.txt"):
                    with open("lifespans.txt", "r") as file:
                        for line in file:
                            # Parse existing file lines to extract pod details
                            parts = line.split("- INFO - Pod Name: ")
                            if len(parts) > 1:
                                timestamp = parts[0].strip()
                                pod_info = parts[1].split(", Age (seconds): ")
                                if len(pod_info) == 2:
                                    pod_name = pod_info[0].strip()
                                    existing_lifespans[pod_name] = datetime.strptime(timestamp,
                                                                                     '%Y-%m-%d %H:%M:%S').replace(
                                        tzinfo=timezone.utc)

                # Prepare updated data
                updated_lifespans = {}
                current_time = datetime.now(timezone.utc)  # Ensure this is offset-aware
                for pod in pod_list.items:
                    if pod.metadata.name.startswith("microservice"):
                        pod_name = pod.metadata.name
                        creation_time = pod.metadata.creation_timestamp

                        # Check if pod already exists
                        if pod_name in existing_lifespans:
                            # Use the existing creation timestamp
                            creation_time = existing_lifespans[pod_name]
                        else:
                            # Append new pods with their creation timestamp
                            creation_time = pod.metadata.creation_timestamp

                        # Calculate age dynamically
                        pod_age_seconds = (current_time - creation_time).total_seconds()

                        # Prepare log entry
                        log_entry = {
                            "creation_timestamp": creation_time,
                            "pod_name": pod_name,
                            "age": pod_age_seconds
                        }
                        updated_lifespans[pod_name] = log_entry

                # Write all pods to lifespans.txt
                with open("lifespans.txt", "w") as file:
                    for pod_name, entry in updated_lifespans.items():
                        file.write(
                            f"{entry['creation_timestamp'].strftime('%Y-%m-%d %H:%M:%S')} - INFO - Pod Name: {entry['pod_name']}, Age (seconds): {entry['age']:.6f}\n"
                        )

                print("Updated lifespans.txt with current pod ages.")
                logging.info("Updated lifespans.txt with current pod ages.")

                #read and print
                print("\n====================================Contents of updated lifespans.txt=======================================")
                with open("lifespans.txt", "r") as file:
                    for line in file:
                        print(line.strip())
                        logging.info("\n====================================Contents of updated lifespans.txt=======================================")
                        logging.info(line.strip())


            except Exception as e:
                print(f"Error fetching or writing pod lifespans: {e}")
                logging.error(f"Error fetching or writing pod lifespans: {e}")
        else:
            print("Insufficient permissions to read the configuration file.")
            logging.error("Insufficient permissions to read the configuration file.")
    else:
        print("K3s configuration file does not exist.")
        logging.error("K3s configuration file does not exist.")

# integration of all functions above together
#################################################################
#################################################################
#################################################################
def take_action(json_input):
    try:
        #print(f"JSON Input to take_action: {json_input}")
        logging.info(f"JSON Input to take_action: {json_input}")
        input_data = handle_incomplete_json(json_input)  # Use the updated handler function

        if input_data is None:
            print("Failed to parse JSON input. JSON is likely incomplete.")
            logging.error("Failed to parse JSON input. JSON is likely incomplete.")
            return

        # Correct the variable name to be consistent
        recommended_action = input_data.get("Recommended Action", {})

        # Handle each pod's CPU, Memory, and Replicas
        for pod_info in recommended_action.get("pods", []):
            pod_name = pod_info.get("name")
            deployment_name = get_deployment_name_from_pod_name(pod_name)

            # Handle CPU and Memory Limits
            if pod_info.get("new_cpu_limit") and pod_info.get("new_memory_limit"):
                update_deployment_resources(json.dumps({
                    "deployment_name": deployment_name,
                    "namespace": "default",
                    "new_cpu_limit": pod_info["new_cpu_limit"],
                    "new_memory_limit": pod_info["new_memory_limit"]
                }))

            # Handle Replicas
            if pod_info.get("new_replicas"):
                update_deployment_replicas(json.dumps({
                    "deployment_name": deployment_name,
                    "namespace": "default",
                    "new_replicas": pod_info["new_replicas"]
                }))

        # Handle Node Placement changes
        if recommended_action.get("new_placement"):
            for placement in recommended_action["new_placement"]:
                deployment_name = get_deployment_name_from_pod_name(placement["pod"])
                update_deployment_node_selector(json.dumps({
                    "deployment_name": deployment_name,
                    "deployment_namespace": "default",
                    "node_selector": {"kubernetes.io/hostname": placement["node"]}
                }))


    except Exception as e:
        print(f"Exception when updating deployment resources: {e}")
        logging.error(f"Exception when updating deployment resources: {e}")



# intent violation part
#################################################################
#################################################################
#################################################################
def process_response_times(data, initial_estimated_rt):
    rt_sfc_data = {}
    index = 0
    #estimated_rt = initial_estimated_rt
    print(f"this is initial estimated rt to be used= {initial_estimated_rt}")
    response_time_sum = 0
    num_requests = 0
    # Make the violation time globally accessible
    global detected_violation_timestamp
    global detected_min_threshold_timestamp
    global violation_handled  # Declare this as global to modify it inside the function
    global min_threshold_handled  # Declare this as global to modify it inside the function
    global retries

    # debug to see the received data from db
    # *******************************************
    try:
        with open("function_received.json", "w") as file:
            json.dump(data, file, indent=4)
        print("Received data saved to 'function_received.json'.")
    except Exception as e:
        print(f"Error saving data to 'function_received.json': {e}")

    # *******************************************

    # Log the current time for filtering
    current_time = datetime.now()
    print(f"Current time for comparison: {current_time}")
    logging.info(f"Current time for comparison: {current_time}")

    # Process each response in the data
    for request_id, details in data.items():
        # Extract relevant details
        end_time = details.get("end_time")
        estimated_rt = details.get("estimated_rt", initial_estimated_rt)  # Fallback to initial if not in details

        if not estimated_rt:
            # Handle the case where 'estimated_rt' is unexpectedly missing
            print(f"Error: 'estimated_rt' missing for request {request_id}. Skipping this entry.")
            logging.error(f"'estimated_rt' missing for request {request_id}. Skipping this entry.")
            continue  # Skip processing this entry

        if not end_time:
            continue

        # Convert string end_time to datetime
        try:
            end_time_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S.%f")
        except ValueError as e:
            print(f"Error parsing end_time '{end_time}' for request_id {request_id}: {e}")
            continue

        # Define the acceptable delay (system delay tolerance)
        acceptable_delay = timedelta(milliseconds=500)  # Adjust this value as per your system's delay tolerance

        # Compare end_time with current_time
        if current_time - end_time_dt <= acceptable_delay:
            print(f"Request {request_id}: end_time {end_time} is within acceptable delay.")
            logging.info(f"Request {request_id}: end_time {end_time} is within acceptable delay.")



            ###





            ##################################### Violation handling part (start) ################################
            #if estimated_rt > intent and not violation_handled and retries > 0:
            if estimated_rt > intent:
                print(
                    f"\033[91m ALARM: Intent Is Violated- Estimated RT exceeds the threshold! ({estimated_rt:.2f} seconds-here violation is based on the moving average) \033[0m")
                logging.warning(
                    f"ALARM: Intent Is Violated- Estimated RT exceeds the threshold! ({estimated_rt:.2f} seconds-here violation is based on the moving average)")

                detected_violation_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                violation_handle_timing = datetime.now()  # Set the handled time
                print(
                    f"Violation detected at {detected_violation_timestamp}- request_id:{request_id}- estimated_rt: {estimated_rt}- end_time {end_time}- current_time: {current_time}")
                logging.info(
                    f"Violation detected at {detected_violation_timestamp}- request_id:{request_id}- estimated_rt: {estimated_rt}- end_time {end_time}- current_time: {current_time}")

                # **Capture violation value**
                violation_estimated_rts.append({
                    "timestamp": detected_violation_timestamp,
                    "estimated_rt": estimated_rt
                })

                # **Save to 'avg_estimated_rt.json'**
                with open("avg_estimated_rt.json", "w") as avg_rt_file:
                    json.dump(violation_estimated_rts, avg_rt_file, indent=2)
                    print(f"Violation data written to: avg_estimated_rt.json")
                    logging.info(f"Violation data written to: avg_estimated_rt.json")

                # Set violation_handled to True to prevent further prompts for this violation
                violation_handled = True
                retries -= 1




                # Preparing all details to send the prompt to GPT
                # =================================================
                # getting monitoring data
                # Call Prometheus data collection
                query_prometheus_for_violation_data()

                # reading whole story of the system
                with open("whole_story_new-flow.txt", "r") as file:
                    content = file.read()
                try:
                    story_details = content.split("FORCED_TEMPLATE:", 1)[0]
                    forced_template = content.split("FORCED_TEMPLATE:", 1)[1]
                except ValueError as e:
                    print(f"Error splitting content in the story file: {e}")
                    logging.error(f"Error splitting content in the story file: {e}")
                    print(f"Content found in the story file:\n{content}")
                    logging.info(f"Content found in the story file:\n{content}")
                    continue

                # calling function to prepare the prompt details
                # =================================================
                # getting cluster info
                collect_and_filter_kubernetes_data()
                generate_microservice_placement(my_input_file, my_output_file)




                ################################# getting network info
                get_switchLinks()
                create_important_hosts_json()
                create_important_links_json()
                get_hosts()
                get_flows()
                generate_more_important_flows()
                ################################################

                # Collecting data from the important JSON files in each call
                with open('k3s_cluster_info_important.json', 'r') as file:
                    cluster_info = json.load(file)




                with open('onos_links_important.json', 'r') as file:
                    network_links = json.load(file)

                with open('onos_hosts_important.json', 'r') as file:
                    network_hosts = json.load(file)



                

                with open('onos_more_important_flows.json', 'r') as file:
                    traffic_flows = json.load(file)
                
                

                with open('monitor_combine_violation_and_previolation_cpu.json', 'r') as file:
                    cpu_data = json.load(file)

                with open('monitor_combine_violation_and_previolation_mem.json', 'r') as file:
                    mem_data = json.load(file)




                with open('monitor_switch_avg_in_utilization.json', 'r') as file:
                    in_traffic_utilization = json.load(file)

                with open('monitor_switch_avg_out_utilization.json', 'r') as file:
                    out_traffic_utilization = json.load(file)

                # save avg response time- first wrap the list in a dictionary with a key
                # getting avg response time from db based on each 30 requests
                average_response_times_dict = {"average_response_times": average_response_times}
                average_response_time = json.dumps(average_response_times_dict, indent=4)

                with open('avg_estimated_rt.json', 'r') as file:
                    avg_estimated_rt = json.load(file)

                # Combine the data into a single dictionary to prepare the prompt
                # =================================================================
                combined_data = {
                    "Cluster Info": cluster_info,
                    "Network Links": network_links,
                    "Network Hosts": network_hosts,
                    "Traffic Flows":traffic_flows,
                    "CPU Utilization": cpu_data,
                    "Memory Utilization": mem_data,
                    "Inbound Switch Traffic": in_traffic_utilization,
                    "Outbound Switch Traffic": out_traffic_utilization,
                    "Average Response Time": avg_estimated_rt
                }

                # Convert combined data to a JSON string (want to pretty-print or truncate if it's too long)
                combined_data_str = json.dumps(combined_data, indent=4)

                # build the prompt by including the combined data

                prompt = (
                    "I have a problem and I need you to solve it completely."
                    f"{story_details}\n\n"
                    "Here is the data collected from the environment:\n"
                    f"{combined_data_str}\n\n"
                    f"What could be the potential root cause and what actions should be taken to mitigate this issue?\n\n"
                    "Please provide your response in just a single complete JSON format containing all recommended actions.\n\n"
                    f"{forced_template}\n\n"
                )
                # print('<<<<<<<<<<<<<<<< here is the complete prompt inside intent watch loop (start) >>>>>>>>>>>>>>>')
                # print complete prompt before sending it to GPT
                # print("Complete Prompt to be sent to ChatGPT:\n", prompt)
                # print('<<<<<<<<<<<<<<<<  here is the complete prompt inside intent watch loop (end) >>>>>>>>>>>>>>>')

                # Build the GPT prompt with all necessary information

                print(">>>>>>>>>>>>>>>>>>>>>>>waiting before sending prompt")
                #time.sleep(120)
                prompt = build_prompt(index, average_response_time, story_details, combined_data)

                print(f"here is the final prompt for today: {prompt}")
                # send prompt to GPT
                chatgpt_response = interact_with_chatgpt(prompt)
                print(
                    '\033[93m ########### Here is the response from GPT after receiving the prompt  (start) ########### \033[0m')
                logging.info(
                    '########### Here is the response from GPT after receiving the prompt  (start) ###########')
                print(chatgpt_response)
                logging.info(chatgpt_response)
                print(
                    '\033[93m ########### Here is the response from GPT after receiving the prompt (end) ########### \033[0m')
                logging.info(
                    '########### Here is the response from GPT after receiving the prompt (end) ###########')

                if chatgpt_response:
                    json_response_str = chatgpt_response.strip('```json').strip('```').strip()
                    json_response_str = clean_json_response(json_response_str)
                    # print(f"ChatGPT Response JSON: {json_response_str}")
                    try:
                        response_json = handle_incomplete_json(json_response_str)

                        if response_json:
                            print(
                                '\033[95m ########### calling take_action function to resolve the violation (start) ########### \033[0m')
                            logging.info(
                                '########### calling take_action function to resolve the violation (start) ###########')




                            #CALL TAKE ACTION FUNCTION
                            #take_action(json.dumps(response_json))







                            print(
                                '\033[95m ########### calling take_action function to resolve the violation (end) ########### \033[0m')
                            logging.info(
                                '########### calling take_action function to resolve the violation (end) ###########')

                            # print(response_json)
                            # break
                        else:
                            print(f"Invalid JSON after cleaning: Unable to parse the response.")
                            logging.error(f"Invalid JSON after cleaning: Unable to parse the response.")
                    except json.JSONDecodeError as e:
                        print(f"Invalid JSON after cleaning: {e}")
                        logging.error(f"Invalid JSON after cleaning: {e}")




                #########################################################
                #########################################################
                ##################wait for 1 minutes after each violation detected cycle
                print("<<<<<<<< waiting for 60s before the next violation cycle>>>>>>>>")
                logging.info("waiting for 60s before the next violation cycle")
                time.sleep(60)

                # Log current time after 3-minute wait
                current_time = datetime.now()
                print(f"Updated current time after 30s  wait: {current_time}")
                logging.info(f"Updated current time after 30s  wait: {current_time}")

                # filtering old data here
                #########################################################
                #########################################################
                violation_handled_time = violation_handle_timing
                print(f"violation_handled_time: {violation_handled_time}")
                logging.info(f"violation_handled_time: {violation_handled_time}")
                filter_start_time = violation_handled_time + timedelta(minutes=3)
                data = {
                    req_id: entry
                    for req_id, entry in data.items()
                    if datetime.strptime(entry["end_time"], "%Y-%m-%d %H:%M:%S.%f") > filter_start_time
                }

                # Continue processing only with new data
                print(f"Filtered data after 30s  wait: {data}")
                logging.info(f"Filtered data after 30s  wait: {data}")



                # Reset the flag to allow the system to check for the next violation
                violation_handled = False
                retries = 1  # reset retries after each cycle
                logging.info('Reset violation_handled an retries for checking the next cycle of violation ')
                ##################################### Violation handling part (end) ################################


            #################### NEW CONDITION (START) ###################
            #if estimated_rt < min_threshold and not min_threshold_handled and retries > 0:
            if estimated_rt < min_threshold:
                print(f"\033[91m ALARM: Estimated RT is below the min threshold! ({estimated_rt:.2f} seconds) \033[0m")
                logging.warning(f"ALARM: Estimated RT is below the min threshold! ({estimated_rt:.2f} seconds)")


                detected_violation_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                violation_handle_timing = datetime.now()  # Set the handled time
                print(f"min_threshold detected at {detected_violation_timestamp}- request_id:{request_id}- estimated_rt: {estimated_rt}- end_time {end_time}- current_time: {current_time}")
                logging.info(f"min_threshold at {detected_violation_timestamp}- request_id:{request_id}- estimated_rt: {estimated_rt}- end_time {end_time}- current_time: {current_time}")


                # **Capture min_threshold value**
                violation_estimated_rts.append({
                    "timestamp": detected_violation_timestamp,
                    "estimated_rt": estimated_rt
                })

                # **Save to 'avg_estimated_rt.json'**
                with open("avg_estimated_rt.json", "w") as avg_rt_file:
                    json.dump(violation_estimated_rts, avg_rt_file, indent=2)
                    print(f" data written to: avg_estimated_rt.json")
                    logging.info(f" data written to: avg_estimated_rt.json")

                # Set min_threshold_handled to True to prevent further prompts for this isuue
                min_threshold_handled = True
                retries -= 1

                # Preparing all details to send the prompt to GPT
                # =================================================
                # getting monitoring data
                # Call Prometheus data collection
                query_prometheus_for_violation_data()

                # reading whole story of the system
                with open("whole_story_new_condition.txt", "r") as file:
                    content = file.read()
                try:
                    story_details = content.split("FORCED_TEMPLATE:", 1)[0]
                    forced_template = content.split("FORCED_TEMPLATE:", 1)[1]
                except ValueError as e:
                    print(f"Error splitting content in the story file: {e}")
                    logging.error(f"Error splitting content in the story file: {e}")
                    print(f"Content found in the story file:\n{content}")
                    logging.info(f"Content found in the story file:\n{content}")
                    continue

                # calling function to prepare the prompt details
                # =================================================
                # getting cluster info
                collect_and_filter_kubernetes_data()

                # getting network info
                get_switchLinks()
                create_important_hosts_json()
                create_important_links_json()
                get_hosts()

                # Collecting data from the important JSON files in each call
                with open('k3s_cluster_info_important.json', 'r') as file:
                    cluster_info = json.load(file)

                with open('onos_links_important.json', 'r') as file:
                    network_links = json.load(file)

                with open('onos_hosts_important.json', 'r') as file:
                    network_hosts = json.load(file)

                with open('monitor_combine_violation_and_previolation_cpu.json', 'r') as file:
                    cpu_data = json.load(file)

                with open('monitor_combine_violation_and_previolation_mem.json', 'r') as file:
                    mem_data = json.load(file)

                with open('monitor_combine_violation_and_previolation_ifin.json', 'r') as file:
                    in_traffic_data = json.load(file)

                with open('monitor_combine_violation_and_previolation_ifout.json', 'r') as file:
                    out_traffic_data = json.load(file)

                # save avg response time- first wrap the list in a dictionary with a key
                # getting avg response time from db based on each 30 requests
                average_response_times_dict = {"average_response_times": average_response_times}
                average_response_time = json.dumps(average_response_times_dict, indent=4)

                with open('avg_estimated_rt.json', 'r') as file:
                    avg_estimated_rt = json.load(file)

                # Combine the data into a single dictionary to prepare the prompt
                # =================================================================
                combined_data = {
                    "Cluster Info": cluster_info,
                    "Network Links": network_links,
                    "Network Hosts": network_hosts,
                    "CPU Utilization": cpu_data,
                    "Memory Utilization": mem_data,
                    "Inbound Switch Traffic": in_traffic_data,
                    "Outbound Switch Traffic": out_traffic_data,
                    "Average Response Time": avg_estimated_rt
                }

                # Convert combined data to a JSON string (want to pretty-print or truncate if it's too long)
                combined_data_str = json.dumps(combined_data, indent=4)

                # build the prompt by including the combined data

                prompt = (
                    "I have a problem and I need you to solve it completely."
                    f"{story_details}\n\n"
                    "Here is the data collected from the environment:\n"
                    f"{combined_data_str}\n\n"
                    f"What could be the potential root cause and what actions should be taken to mitigate this issue?\n\n"
                    "Please provide your response in just a single complete JSON format containing all recommended actions.\n\n"
                    f"{forced_template}\n\n"
                )
                # print('<<<<<<<<<<<<<<<< here is the complete prompt inside intent watch loop for the new condition (start) >>>>>>>>>>>>>>>')
                # print complete prompt before sending it to GPT
                # print("Complete Prompt to be sent to ChatGPT for the new condition:\n", prompt)
                # print('<<<<<<<<<<<<<<<<  here is the complete prompt inside intent watch loop for the new condition (end) >>>>>>>>>>>>>>>')

                # Build the GPT prompt with all necessary information
                prompt = build_prompt_new_condition(index, average_response_time, story_details, combined_data)
                # send prompt to GPT
                chatgpt_response = interact_with_chatgpt(prompt)
                print(
                    '\033[93m ########### Here is the response from GPT after receiving the prompt for the new function  (start) ########### \033[0m')
                logging.info(
                    '########### Here is the response from GPT after receiving the prompt for the new function  (start) ###########')
                print(chatgpt_response)
                logging.info(chatgpt_response)
                print(
                    '\033[93m ########### Here is the response from GPT after receiving the prompt for the new function (end) ########### \033[0m')
                logging.info(
                    '########### Here is the response from GPT after receiving the prompt for the new function (end) ###########')

                if chatgpt_response:
                    json_response_str = chatgpt_response.strip('```json').strip('```').strip()
                    json_response_str = clean_json_response(json_response_str)
                    # print(f"ChatGPT Response JSON: {json_response_str}")
                    try:
                        response_json = handle_incomplete_json(json_response_str)

                        if response_json:
                            print('\033[95m ########### calling take_action function to resolve the issue (start) ########### \033[0m')
                            logging.info(
                                '########### calling take_action function to resolve the issue (start) ###########')
                            take_action(json.dumps(response_json))
                            print(
                                '\033[95m ########### calling take_action function to resolve the issue (end) ########### \033[0m')
                            logging.info(
                                '########### calling take_action function to resolve the issue (end) ###########')

                            # print(response_json)
                            # break
                        else:
                            print(f"Invalid JSON after cleaning: Unable to parse the response.")
                            logging.error(f"Invalid JSON after cleaning: Unable to parse the response.")
                    except json.JSONDecodeError as e:
                        print(f"Invalid JSON after cleaning: {e}")
                        logging.error(f"Invalid JSON after cleaning: {e}")

                ##################wait for 1 minutes after each min_threshold detected cycle
                print("<<<<<<<< waiting for 60s  before the next min_threshold cycle>>>>>>>>")
                logging.info("waiting for 60s  before the next min_threshold cycle")
                time.sleep(60)

                # Log current time after 1-minute wait
                current_time = datetime.now()
                print(f"Updated current time after 30s  wait: {current_time}")
                logging.info(f"Updated current time after 30s  wait: {current_time}")

                # filtering old data here
                #########################################################
                #########################################################
                violation_handled_time = violation_handle_timing
                print(f"min_threshold_handled_time: {violation_handled_time}")
                logging.info(f"min_threshold_handled_time: {violation_handled_time}")
                filter_start_time = violation_handled_time + timedelta(minutes=3)
                data = {
                    req_id: entry
                    for req_id, entry in data.items()
                    if datetime.strptime(entry["end_time"], "%Y-%m-%d %H:%M:%S.%f") > filter_start_time
                }

                # Continue processing only with new data
                print(f"Filtered data after 30s  wait: {data}")
                logging.info(f"Filtered data after 30s  wait: {data}")

                # Reset the flag to allow the system to check for the next min_threshold
                min_threshold_handled = False
                retries = 1  # reset retries after each cycle
                logging.info('Reset min_threshold_handled an retries for checking the next cycle of min_threshold ')

    #################### NEW CONDITION (END) ###################








            timestamps.append(index)

            # Save the summary data
            with open(summary_output_file, "w") as output_file:
                json.dump(rt_sfc_data, output_file, indent=2)


# generate plot for response time
#################################################################
#################################################################
#################################################################
def plot_response_times(timestamps, response_times, estimated_rts):
    # Debugging: Print length of the data arrays before plotting
    # print(f"Length of timestamps: {len(timestamps)}")
    # print(f"Length of response_times: {len(response_times)}")
    # print(f"Length of estimated_rts: {len(estimated_rts)}")

    if len(timestamps) == 0 or len(response_times) == 0 or len(estimated_rts) == 0:
        print("Error: One of the plotting arrays is empty, nothing to plot.")
        logging.error("Error: One of the plotting arrays is empty, nothing to plot.")
        return

    # Find the minimum length among the arrays
    min_length = min(len(timestamps), len(response_times), len(estimated_rts))

    # Truncate all arrays to the minimum length
    timestamps = timestamps[:min_length]
    response_times = response_times[:min_length]
    estimated_rts = estimated_rts[:min_length]

    plt.plot(timestamps, response_times, linestyle='-', label='Response Time')
    plt.plot(timestamps, estimated_rts, linestyle='-', color='red', label='Estimated RT')
    plt.xlabel("Index")
    plt.ylabel("Response Time (s)")
    plt.ylim(0)
    plt.xlim(0)
    plt.legend()
    plt.tight_layout()
    # plt.show()




########################new plot function
def plot_response_times_with_time(data):
    """
    Plot response times and estimated RTs against timestamps (x-axis in time format).
    """
    try:
        # Initialize lists for data
        timestamps = []
        response_times = []
        estimated_rts = []

        # Parse the data
        for request_id, details in data.items():
            response_time = details.get("response_time")
            estimated_rt = details.get("estimated_rt")
            end_time = details.get("end_time")

            if response_time is None or estimated_rt is None or end_time is None:
                print(f"Skipping entry {request_id}: Missing response_time, estimated_rt, or end_time.")
                continue

            try:
                # Convert end_time to datetime object
                end_time_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S.%f")
                timestamps.append(end_time_dt)

                # Convert response_time to seconds
                response_time_dt = datetime.strptime(response_time, "%H:%M:%S.%f")
                response_time_seconds = (response_time_dt - datetime(1900, 1, 1)).total_seconds()

                # Append data to the plotting arrays
                response_times.append(response_time_seconds)
                estimated_rts.append(estimated_rt)

            except ValueError as e:
                print(f"Error processing entry {request_id}: {e}")
                continue

        # Check if there is data to plot
        if not timestamps or not response_times or not estimated_rts:
            print("Error: No valid data available for plotting.")
            return

        # Plot the data
        plt.figure(figsize=(10, 6))
        plt.plot(timestamps, response_times, label="Response Time (s)", linestyle="-")
        plt.plot(timestamps, estimated_rts, label="Estimated RT (s)", linestyle="-", color="red")
        plt.xlabel("Time")
        plt.ylabel("Response Time (s)")
        plt.title("Response Times vs Time")
        plt.legend()
        plt.grid(True)
        plt.tight_layout()

        # Rotate x-axis labels for better readability
        plt.xticks(rotation=45)

        # Show the plot
        plt.show()
    except Exception as e:
        print(f"An error occurred while plotting: {e}")



# Main logic to fetch data and process response times
last_checked_end_time = None  # Initialize to track the latest processed end_time

try:
    while True:
        # Step 1: Fetch data from db
        response = requests.get(db_url_get_time)

        # Step 2: Check if request was successful
        if response.status_code == 200:
            print('GET request to DB is successful')
            logging.info('GET request to DB is successful')

            # Step 3: Write the data to 'output_file'
            with open(output_file, "w") as file:
                file.write(response.text)  # Write API response to file
                # print(f'Data written to the file: {output_file}')

            ############################################################################
            ############################################################################
            with open("all_data_from_DB.json", "w") as file:
                json.dump(json.loads(response.text), file, indent=4)  # Parse the string to JSON


            # Step 5: Read data from "all_data_from_DB.json" and extract response_time and end_time of microservice4
            with open("all_data_from_DB.json", "r") as db_file:
                all_data = json.load(db_file)  # Load data from the file

                all_response_time_data = {}
                estimated_rt = None  # Initialize for estimated_rt calculation

                for request_id, value in all_data.items():
                    response_time = value.get("response_time", None)
                    microservice4_data = value.get("microservice4", {})
                    end_time = microservice4_data.get("end_time", None)

                    # Calculate `estimated_rt` using the provided formula
                    if response_time:
                        try:
                            # Convert `response_time` to seconds for estimated_rt calculation
                            response_time_dt = datetime.strptime(response_time, "%H:%M:%S.%f")
                            response_time_seconds = (response_time_dt - datetime(1900, 1, 1)).total_seconds()

                            if estimated_rt is None:
                                estimated_rt = response_time_seconds  # Initialize with the first response time
                            else:
                                estimated_rt = (1 - alpha) * estimated_rt + alpha * response_time_seconds

                            # Save response_time and end_time in their original formats
                            if end_time:
                                all_response_time_data[request_id] = {
                                    "response_time": response_time,  # Store as original string
                                    "end_time": end_time,  # Store as original string
                                    "estimated_rt": estimated_rt,
                                }
                        except ValueError as e:
                            print(f"Error processing response_time: {e}")
                            logging.error(f"Error processing response_time: {e}")

                with open("all_response_time.json", "w") as response_time_file:
                    json.dump(all_response_time_data, response_time_file, indent=4)

                print(
                    "Extracted response_time (original format), end_time (original format), and calculated estimated_rt written to 'all_response_time.json'."
                )
                logging.info(
                    "Extracted response_time (original format), end_time (original format), and calculated estimated_rt written to 'all_response_time.json'."
                )

            # filter last minutes data and initialized estimated_rt
            ##################################################
            ##################################################
            with open("all_response_time.json", "r") as file:
                all_response_time_data = json.load(file)

            # Get the current time
            current_time = datetime.now()
            print(f"current_time: {current_time}")
            logging.info(f"current_time: {current_time}")


            one_minute_ago = current_time - timedelta(minutes=1)
            print(f"one_minute_ago: {one_minute_ago}")
            logging.info(f"one_minute_ago: {one_minute_ago}")

            initial_estimated_rt = None
            last_minute_data = {}
            new_last_checked_end_time = None
            latest_end_time_before_last_minute = None  # To track the latest end_time before one_minute_ago

            for request_id, entry in all_response_time_data.items():
                end_time = entry["end_time"]
                if end_time is not None:
                    try:
                        end_time_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S.%f")
                        # Update `initial_estimated_rt` if the entry is before one_minute_ago
                        if end_time_dt <= one_minute_ago:
                            if latest_end_time_before_last_minute is None or end_time_dt > latest_end_time_before_last_minute:
                                latest_end_time_before_last_minute = end_time_dt
                                initial_estimated_rt = entry["estimated_rt"]

                        # Check if the end_time is within the last minute
                        if one_minute_ago <= end_time_dt <= current_time:
                            # Include data even if processed previously, as we need all last-minute data
                            last_minute_data[request_id] = entry
                            if new_last_checked_end_time is None or end_time_dt > new_last_checked_end_time:
                                new_last_checked_end_time = end_time_dt

                        else:
                            # Log excluded entries and their reason
                            if end_time_dt < one_minute_ago:
                                reason = "before one_minute_ago"
                            elif end_time_dt > current_time:
                                reason = "after current_time"
                            else:
                                reason = "unknown reason"
                            #print(f"Excluded: {request_id}, end_time: {end_time_dt}, reason: {reason}")
                            #logging.info(f"Excluded: {request_id}, end_time: {end_time_dt}, reason: {reason}")
                    except ValueError as e:
                        print(f"Error processing end_time for {request_id}: {e}")
                        logging.error(f"Error processing end_time for {request_id}: {e}")

            #
            # Additional filtering based on violation handling time
            # Step 2: Additional filtering based on violation handling time
            if last_minute_data and violation_handled_time:
                filter_start_time = violation_handled_time + timedelta(minutes=3)
                filtered_data = {}
                for req_id, entry in last_minute_data.items():
                    end_time = entry["end_time"]
                    try:
                        end_time_dt = datetime.strptime(end_time, "%Y-%m-%d %H:%M:%S.%f")
                        if end_time_dt > filter_start_time:
                            filtered_data[req_id] = entry
                        else:
                            print(f"Filtered out due to violation_handled_time: {req_id}, end_time: {end_time_dt}")
                            logging.info(
                                f"Filtered out due to violation_handled_time: {req_id}, end_time: {end_time_dt}")
                    except ValueError as e:
                        print(f"Error processing end_time for {req_id}: {e}")
                        logging.error(f"Error processing end_time for {req_id}: {e}")

                last_minute_data = filtered_data

            # Step 3: Save filtered last-minute data
            with open("last_minutes_data.json", "w") as file:
                json.dump(last_minute_data, file, indent=4)


            # Save initial_estimated_rt
            if initial_estimated_rt is not None:
                with open("initial_estimated_rt.json", "w") as file:
                    json.dump({"initial_estimated_rt": initial_estimated_rt}, file, indent=4)

            print("Filtered last-minute data saved to 'last_minutes_data.json'.")
            print("Initial estimated_rt value saved to 'initial_estimated_rt.json'.")

            # Step: Call `process_response_times` with filtered data and `initial_estimated_rt`
            if last_minute_data:  # Only process if there is new data
                print(f"Passing initial_estimated_rt: {initial_estimated_rt}")
                process_response_times(last_minute_data, initial_estimated_rt)

            # Update the last checked end_time
            last_checked_end_time = new_last_checked_end_time







            ############################################################################
            ############################################################################

            # Step 4: Check if the file was successfully created
            if os.path.exists(output_file):
                print(f"The file {output_file} exists after writing.")
                logging.info(f"The file {output_file} exists after writing.")
            else:
                print(f"Error: The file {output_file} does not exist after writing.")
                logging.error(f"Error: The file {output_file} does not exist after writing.")
                raise FileNotFoundError(f"{output_file} not found after writing.")

            # Step 5: Read the data back from the file for processing
            with open(output_file, "r") as input_file:
                data = json.load(input_file)  # Load JSON data from the file
                # print("Data successfully read from file:", output_file)



            # Step 7: Write summary data to 'summary_output_file'
            with open(summary_output_file, "w") as summary_file:
                json.dump(data, summary_file, indent=2)
                # print(f"Summary data written to: {summary_output_file}")

            # Step 8: Save the average response times to 'average_rt_file'
            with open(average_rt_file, "w") as avg_file:
                json.dump({"average_response_times": average_response_times}, avg_file, indent=2)
                # print(f"Average response times written to: {average_rt_file}")

            ###############################################
            ###############################################
            ###############################################
            ###############################################
            try:
                with open("all_response_time.json", "r") as file:
                    all_response_time_data = json.load(file)
            except FileNotFoundError as e:
                print(f"Error: 'all_response_time.json' not found: {e}")
                logging.error(f"Error: 'all_response_time.json' not found: {e}")
                break
            except json.JSONDecodeError as e:
                print(f"Error decoding JSON from 'all_response_time.json': {e}")
                logging.error(f"Error decoding JSON from 'all_response_time.json': {e}")
                break

            #
            # Prepare data for plotting
            timestamps.clear()
            response_times.clear()
            estimated_rts.clear()
            index = 0  # Initialize index for timestamps

            for request_id, details in all_response_time_data.items():
                response_time = details.get("response_time")
                estimated_rt = details.get("estimated_rt")
                end_time = details.get("end_time")

                if response_time is None or estimated_rt is None or end_time is None:
                    print(f"Skipping entry {request_id}: Missing response_time, estimated_rt, or end_time.")
                    logging.warning(f"Skipping entry {request_id}: Missing response_time, estimated_rt, or end_time.")
                    continue
                try:
                    # Convert response_time to seconds
                    response_time_dt = datetime.strptime(response_time, "%H:%M:%S.%f")
                    response_time_seconds = (response_time_dt - datetime(1900, 1, 1)).total_seconds()

                    # Append data to the plotting arrays
                    timestamps.append(index)
                    response_times.append(response_time_seconds)
                    estimated_rts.append(estimated_rt)
                    index += 1
                except ValueError as e:
                    print(f"Error processing response_time for {request_id}: {e}")
                    logging.error(f"Error processing response_time for {request_id}: {e}")
                    continue


            # Step 9: Plot the response times
            #print(f"this is timestamps, {timestamps}")
            #print(f"this is response_times, {response_times}")
            #print(f"this is estimated_rts, {estimated_rts}")
            plot_response_times(timestamps, response_times, estimated_rts)
            #plot_response_times_with_time(all_response_time_data)

            ###############################################
            ###############################################
            ###############################################
            ###############################################


            # Step 10: Save the estimated RTs to 'estimated_rt.json'
            estimated_rt_file = "estimated_rt.json"
            with open(estimated_rt_file, "w") as rt_file:
                json.dump({"estimated_rts": estimated_rts}, rt_file, indent=2)
                # print(f"Estimated RT values written to: {estimated_rt_file}")

        else:
            print(f"Failed to retrieve data: {response.status_code}")
            logging.error(f"Failed to retrieve data: {response.status_code}")



except requests.exceptions.RequestException as e:
    print(f"Request failed: {e}")
    logging.error(f"Request failed: {e}")
# except FileNotFoundError:
# print(f"Error: File '{output_file}' not found.")
except json.JSONDecodeError as e:
    print(f"Error decoding JSON: {e}")
    logging.error(f"Error decoding JSON: {e}")
except Exception as e:
    print(f"An error occurred: {e}")
    logging.error(f"An error occurred: {e}")
