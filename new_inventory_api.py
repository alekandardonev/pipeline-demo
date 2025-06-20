# NEW_INVENTORY_API.PY CONTEXT
#
# ------------------------------------------------------------------------------------------------------------------------------------------------------------
# AWS Inventory Script
# ------------------------------------------------------------------------------------------------------------------------------------------------------------
#
# This script performs an inventory of AWS resources across multiple regions using multithreading.
# It retrieves resource details, handles exceptions, and logs the progress and results.
#
# Modules:
#    threading: Provides threading capabilities.
#    boto3: AWS SDK for Python.
#    json: JSON handling.
#    yaml: YAML handling.
#    os: OS-related functions.
#    sys: System-specific parameters and functions.
#    datetime: Date and time handling.
#    time: Time-related functions.
#    argparse: Command-line argument parsing.
#    multiprocessing: Process-based parallelism.
#    concurrent.futures: High-level interface for asynchronously executing callables.
#    tqdm: Progress bar library.
#    glob: Unix-style pathname pattern expansion.
#    utils: Custom utility functions.
#    botocore.exceptions: Exceptions for AWS SDK.
#
# Classes:
#    InventoryThread: Thread class for performing inventory tasks.
#
# Functions:
#    get_all_regions: Retrieve all AWS regions.
#    test_region_connectivity: Test connectivity to a specific AWS region.
#    detail_handling: Handle the details of inventory items by calling specified detail functions on the client.
#    inventory_handling: Handle the inventory retrieval and processing for a specified AWS resource and region.
#    list_used_resources: List used resources based on the provided YAML files.
#
# Usage:
#    Run the script with appropriate command-line arguments to perform the inventory.
#    Example: python new_inventory_api.py --resource-dir resources --with-meta --with-extra --with-empty


# ------------------------------------------------------------------------------

# Import required modules

# ------------------------------------------------------------------------------

import threading
import boto3
import json
import yaml
import os
import sys
from datetime import datetime
import time
import argparse
import multiprocessing
from concurrent.futures import ThreadPoolExecutor
from tqdm import tqdm
import glob
from utils import write_log, transform_function_name, json_serial, is_empty, get_all_regions, test_region_connectivity  # Importer les fonctions utilitaires
from botocore.exceptions import EndpointConnectionError, ClientError  # Ajout des exceptions

# ------------------------------------------------------------------------------

# Utility operations

# ------------------------------------------------------------------------------

# Ensure log directory exists
log_dir = "log"
if not os.path.exists(log_dir):
    os.makedirs(log_dir)

# Ensure output directory exists
output_dir = "output"
if not os.path.exists(output_dir):
    os.makedirs(output_dir)

# Create a timestamped log file
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
log_file_path = os.path.join(log_dir, f"log_{timestamp}.log")

# Global variables
boto3_clients = {}  # Dictionary to store boto3 clients for different resources and regions
results = {}  # Dictionary to store the inventory results
account_id = None  # AWS account ID

# Progress counters
total_tasks = 0  # Counter for the total number of tasks
completed_tasks = 0  # Counter for the number of completed tasks
progress_bar = None  # Progress bar object

# resource response counters
successful_resources = 0  # Counter for the number of successful resource responses
failed_resources = 0  # Counter for the number of failed resource responses
skipped_resources = 0  # Counter for skipped resources
empty_resources = 0
filled_resources = 0

# Determine the number of CPU cores
num_cores = multiprocessing.cpu_count()

# Set the number of threads to 2 to 4 times the number of CPU cores
num_threads = num_cores * 4  # You can adjust this multiplier based on your needs
# num_threads = 1 # For test purposes with no MT

# ------------------------------------------------------------------------------

# Multithreading Class

# ------------------------------------------------------------------------------

class InventoryThread(threading.Thread):

    """Thread class for performing inventory tasks."""

    def __init__(self, category, region_name, resource, boto_resource_name, node_details, key, progress_callback):
        """
        Initialize a new instance of the class.
        Args:
            category (str): The category of the resource.
            region_name (str): The name of the AWS region.
            resource (str): The specific resource type.
            boto_resource_name (str): The name of the boto resource.
            node_details (dict): Details about the node.
            key (str): The key associated with the resource.
            progress_callback (callable): A callback function to report progress.
        """

        threading.Thread.__init__(self)
        self.category = category
        self.region_name = region_name
        self.resource = resource
        self.boto_resource_name = boto_resource_name
        self.key = key
        self.node_details = node_details
        self.progress_callback = progress_callback

    def run(self):
        """Run the inventory task."""
        inventory_handling(self.category, self.region_name, self.resource, self.boto_resource_name, self.node_details, self.key, self.progress_callback)

# ------------------------------------------------------------------------------

# Inventory Management Functions

# ------------------------------------------------------------------------------

def detail_handling(client, inventory, node_details, resource, key):

    """
    Handles the details of inventory items by calling specified detail functions on the client.

    Args:
        client (object): The client object used to call detail functions.
        inventory (dict): The inventory containing items to be processed.
        node_details (dict): The node in the inventory specifying details to be retrieved.
        resource (str): The type of resource being processed (e.g., 's3').
        account_id (str): The account ID used for certain resource types.

    Raises:
        ClientError: If an error occurs while calling the detail function on the client.

    Returns:
        None
    """

    global account_id, with_empty

    inventory_item = inventory[key]

    for index, item in enumerate(inventory_item):

        the_node_details = node_details[key]['details']

        for detail in the_node_details:

            the_details = the_node_details[detail]

            item_search_id = the_details['item_search_id']
            detail_function = the_details['detail_function']
            detail_param = the_details['detail_param']   
            complementary_params = the_details.get('complementary_param', None)

            # Loop over the inventory items to retrieve the detail

            if isinstance(item, tuple):
                if item[1]:
                    detail_param_value = item[1]
                else:
                    detail_param_value = item.get(item_search_id, None)
            elif isinstance(item, dict):
                detail_param_value = item[item_search_id]
            elif isinstance(item, list):
                detail_param_tmp = item[0]
                if isinstance(detail_param_tmp, str):
                    detail_param_value = detail_param_tmp
                else:
                    detail_param_value = detail_param_tmp[detail_param]
            else:
                detail_param_value = item

            # exception for s3 location: we need an additionnal arg (account id)

            if resource == 's3' and detail_function == 'get_bucket_location':
                if not complementary_params:
                    complementary_params = {'ExpectedBucketOwner': account_id}
                else:
                    complementary_params.update({'ExpectedBucketOwner': account_id})

            # General case

            detail_response = {}

            # Calling all the corresponding detail resources (see 'extra_resource_call.json')

            try:

                if complementary_params:
                    detail_response = client.__getattribute__(detail_function)(**{detail_param: detail_param_value, **complementary_params})
                    write_log(f"Calling detail {detail_function} with params {detail_param}: {detail_param_value} and complementary params: {complementary_params}", log_file_path)
                else:
                    detail_response = client.__getattribute__(detail_function)(**{detail_param: detail_param_value})
                    write_log(f"Calling detail {detail_function} with params {detail_param}: {detail_param_value}", log_file_path)
                if not with_meta:
                    detail_response.pop('ResponseMetadata', None)

            except ClientError as e1:

                exception_function_name = transform_function_name(e1.operation_name)
                if exception_function_name != detail_function:
                    raise e1

            except Exception as e2:

                write_log(f"Error (e2) calling detail {detail_function} with params {detail_param}: {detail_param_value}: {e2} ({type(e2)})", log_file_path)

            # The response is sometimes empty, sometimes a string, sometimes a list or a dict

            if len(detail_response) > 0 or with_empty:

                if detail != "":
                    if detail != 'None':
                        detail_response = {detail: detail_response[detail]}
                else:
                    detail_response = {detail: detail_response}

                # Now we add the detail to the inventory

                try:
                    if isinstance(item, tuple):
                        item[1][1].update(detail_response)
                    elif isinstance(item, dict):
                        if detail in item and isinstance(item[detail], dict):
                            item[detail].update(detail_response[detail])
                        else:
                            item.update(detail_response)
                    elif isinstance(item, list):
                        tmp_item = item[0]
                        if isinstance(tmp_item, str):
                            item = [tmp_item, detail_response]
                        elif isinstance(tmp_item, dict):
                            if detail in tmp_item and isinstance(tmp_item[detail], dict):
                                tmp_item[detail].update(detail_response[detail])
                            else:
                                tmp_item.update(detail_response)
                        else:
                            item[0] = detail_response
                    elif isinstance(item, str):
                        inventory[key][index] = [item, detail_response]
                    else:
                        raise TypeError(f"Unsupported item type: {type(item)}")
                except (TypeError, AttributeError) as ei:
                    write_log(f"Error updating inventory with detail {detail_response}: {ei} ({type(ei)})", log_file_path)
            pass

# ------------------------------------------------------------------------------


def inventory_handling(category, region_name, resource, boto_resource_name, node_details, key, progress_callback):

    """
    Handles the inventory process for a given AWS resource in a specified region.

    Parameters:
        category (str): The category of the resource.
        region (str): The AWS region where the resource is located.
        resource (str): The type of AWS resource to inventory.
        boto_resource_name (str): The name used in boto3 (genrally the same, but you have surprises)
        node_details (dict): Details about the node, including the function to call and any additional details.
        key (str): A key used for logging or identification purposes.
        progress_callback (function): A callback function to report progress.
    
    Returns:
        None

    Raises:
        AttributeError: If there is an attribute error during the inventory process.
        ClientError: If there is a client error during the inventory process.
        EndpointConnectionError: If there is a connection error during the inventory process.
        Exception: For any other exceptions that occur during the inventory process.

    Notes:
    - This function logs the start and end of the inventory process.
    - It handles specific exceptions for EC2 resources that require a region even when global.
    - The function supports threading and reports progress through the progress_callback.
    - It constructs the inventory and handles empty results based on the provided arguments.
    - Detailed resource information can be retrieved if specified in node_details.
    """

    # --- Main body of the 'inventory_handling' function

    global account_id, results, successful_resources, failed_resources, skipped_resources, empty_resources, filled_resources

    pass
    write_log(f"Starting inventory for {resource} in {region_name}", log_file_path)

    try:

        # --- Some exceptions for EC2 that needs a region even when global (ex : DescribeRegions)

        if region_name != 'global':
            client = boto3.client(boto_resource_name.lower(), region_name=region_name)
        else:
            if resource == 'ec2':
                client = boto3.client(boto_resource_name.lower(), region_name='us-east-1')
            else:
                client = boto3.client(boto_resource_name.lower())

        # --- Inventory call for the resource. Reminder: called through threading

        # RAJOUTER BOUCLE sur node_details, pour chacun des détails

        for item in node_details:

            # Each resource can have multiple nodes. Generally, only one node is used, but some resources require multiple nodes to retrieve all the information.

            node = node_details[item]
            func = node['function']

            try:

                # --- API call for the resource 
                start_time = time.time()
                inventory = client.__getattribute__(func)()
                end_time = time.time()
                successful_resources += 1
                write_log(f"API call for {resource} in {region_name} for {func} took {end_time - start_time:.2f} seconds", log_file_path)
            
            except AttributeError as e1:

                write_log(f"Error (1) querying {resource} in {region_name} using {node_details['function']}: {e1} ({type(e1)})", log_file_path)
                failed_resources += 1

            except ClientError as e2:

                if type(e2).__name__ == 'AWSOrganizationsNotInUseException':

                    write_log(f"Warning (2): Skipping {resource} in {region_name} due to organizations not in use error: {e2} ({type(e2)})", log_file_path)
                    skipped_resources += 1

                else:

                    write_log(f"Error (3) querying {resource} in {region_name} using {node_details['function']}: {e2} ({type(e2)})", log_file_path)
                    failed_resources += 1

            except EndpointConnectionError as e3:

                write_log(f"Warning (4): Skipping {resource} in {region_name} due to connection error: {e3} ({type(e3)})", log_file_path)
                skipped_resources += 1


            except Exception as e:

                write_log(f"Error (e) querying {resource} in {region_name} using {node_details['function']}: {e} ({type(e)})", log_file_path)
                failed_resources += 1

            # --- Cmd line arg "with-meta" ('ResponseMetaData)

            if not with_meta:
                response_metadata = inventory.pop('ResponseMetadata', None)

            # --- Constructing the inventory

            empty_items = True # a result is considered as 'empty' if it has no interesting value ('NextToken' or 'ResponseMetada' have no intersting value for an inventory)

            for key, value in inventory.items():
                if not is_empty(value) and key not in {'NextToken'}:
                    empty_items = False
                    break

            object_type = list(inventory.keys())[0] if inventory else 'Unknown'
            if category not in results:
                results[category] = {}
            if resource not in results[category]:
                results[category][resource] = {}
            if object_type not in results[category][resource]:
                results[category][resource][object_type] = {}

            if not empty_items or with_empty:

				# --- Here: not empty, or we want to list the empty values too (arg 'with_empty')# --- Here: not empty, or we want to list the empty values too (arg 'with_empty')# --- Here: not empty, or we want to list the empty values too (arg 'with_empty')# --- Here: not empty, or we want to list the empty values too (arg 'with_empty')# --- Here: not empty, or we want to list the empty values too (arg 'with_empty')# --- Here: not empty, or we want to list the empty values too (arg 'with_empty')# --- Here: not empty, or we want to list the empty values too (arg 'with_empty')# --- Here: not empty, or we want to list the empty values too (arg 'with_empty')

                if region_name not in results[category][resource][object_type]:
                    results[category][resource][object_type][region_name] = {}

                start_time = time.time()

                inventory.pop('NextToken', None) # no "NextToken"
                results[category][resource][object_type][region_name] = inventory[object_type]
                if with_meta and response_metadata:
                    # MetaData only if the key is present and if we asked for it (arg 'with_meta')
                    results[category][resource][object_type][region_name]['ResponseMetadata'] = response_metadata

                end_time = time.time()
                write_log(f"Processing results for {resource} in {region_name} for function {func} took {end_time - start_time:.2f} seconds", log_file_path)
                filled_resources += 1

                # --- In case of: we want more information about the resource
                #     Calling all the corresponding detail resources 

                detail_handling(client, inventory, node_details, resource, key)

            else:

                # The inventory is empty, but don't forget to count (for the progression bar)
                empty_resources += 1
                write_log(f"Empty results for {resource} in {region_name}", log_file_path)

    except Exception as e:

        write_log(f"Error (e) querying {resource} in {region_name} using {node_details['function']}: {e} ({type(e)})", log_file_path)

    finally:

        progress_callback(1)
        write_log(f"End of inventory for {resource} in {region_name} using {node_details['function']}", log_file_path)

# ------------------------------------------------------------------------------

def resource_inventory(progress_callback, thread_list, category, resource, boto_resource_name, node_details, region_name):

    """
    Collects resource inventory for a specified AWS resource and region, and updates progress.

    Args:
        progress_callback (function): A callback function to update progress.
        thread_list (list): A list to store threads for concurrent execution.
        category (str): The category of the AWS resource.
        resource (str): The AWS resource to query.
        boto_resource_name (str): The name of the boto resource.
        functions (list): A list of functions to execute for the resource.
        node_details (list): A list to store inventory nodes.
        region_name (str): A AWS region name.

    Returns:
        None
    """

    global total_tasks

    for node_name in node_details:

        write_log(f"Querying category: {category}, resource: {resource}, node_name: {node_name}, region: {region_name}", log_file_path)
        total_tasks += 1  # Increment total_tasks for each sub-task. One for each attribute in the node_details
        # total_tasks += len(node_details) * len(node_details[node_name]['details'])  # Increment for each detail function call
        thread = InventoryThread(category, region_name, resource, boto_resource_name, node_details, f"{resource} in {region_name}", progress_callback)
        print('.', end='')
        thread_list.append(thread)


# ------------------------------------------------------------------------------

def list_used_resources(inventory_structure):

    """
    List used resources based on the provided YAML files.

    This function performs the following steps:
    1. Retrieves all AWS regions.
    2. Creates a structure of resources based on the provided IAM policy files.
    3. Initializes and manages threads to query both global and regional resources.
    4. Updates a progress bar to reflect the progress of the inventory process.
    5. Logs the execution time and results of the inventory process.
    6. Writes the results to a JSON file.

    Args:
        inventory_structure (list): A structure of resources based on the provided YAML file with all informations about the resources.

    Returns:
        dict: A dictionary of the used resources.
    """

    # ------------------------------------------------------------------------------

    def progress_callback(amount):
        
        """
        Callback function to update the progress bar.

        Args:
            amount (int): The amount by which to update the progress bar.
        """

        progress_bar.update(amount)

 

    # ------------------------------------------------------------------------------

    global account_id, results, total_tasks, progress_bar, successful_resources, failed_resources, filled_resources, empty_resources

    start_time = time.time()

    # --- Get AWS regions

    regions = get_all_regions()

    if not regions:
        write_log("Unable to retrieve the list of regions.", log_file_path)
        return

    thread_list = []

    # --- Retrieve the AWS account ID using STS

    sts_client = boto3.client('sts')
    account_id = sts_client.get_caller_identity()["Account"]

    # --- Modify the JSON file path to include the account ID

    json_file_path = os.path.join(output_dir, f"inventory_{account_id}_{timestamp}.json")

    # --- Handle all resources

    for resource_info in inventory_structure:

        for resource in resource_info:

            inventory_info = resource_info[resource]

            category = inventory_info.get('category', 'unknown')
            boto_resource_name = inventory_info.get('boto_resource_name', [])
            regions_type = inventory_info.get('region_type', ['local'])
            node_details = inventory_info.get('inventory_nodes', [])

            if 'global' in regions_type:

                # For global resources, we only need to query once
                resource_inventory(progress_callback, thread_list, category, resource, boto_resource_name, node_details, 'global')

            else:

                # For local resources, we need to query each region

                for region in regions:
                    resource_inventory(progress_callback, thread_list, category, resource, boto_resource_name, node_details, region['RegionName'])

    # --- Initialize progress bar with the total number of sub-tasks

    print()
    progress_bar = tqdm(total=total_tasks, desc="Inventory Progress", unit="sub-task")

    # --- Use ThreadPoolExecutor to manage the threads

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        for thread in thread_list:
            executor.submit(thread.run)

    progress_bar.close()

    # --- Include the list of regions if --with-extra is specified

    if with_extra:
        results['regions'] = regions

    end_time = time.time()
    execution_time = end_time - start_time

    # --- Display summary of the inventory process

    print(f"\nTotal execution time: {execution_time:.2f} seconds")
    print(f"Total resources called: {total_tasks}")  # Divide by 2 to get the actual number of resources called
    print(f"Successful resources: {successful_resources} ({filled_resources} resources with datas, {empty_resources} empty resources)")
    print(f"Failed resources: {failed_resources}")
    print(f"Skipped resources: {skipped_resources}")
    
    # --- Write the results to a JSON file

    with open(json_file_path, "w") as json_file:
        json.dump(results, json_file, indent=4, default=json_serial)

    return results


# ------------------------------------------------------------------------------

# Main Function

# ------------------------------------------------------------------------------

if __name__ == "__main__":

    # --- Handle command-line arguments

    parser = argparse.ArgumentParser(description='AWS Inventory Script')
    parser.add_argument('--resource-dir', type=str, default='resources', help='The directory containing the resource files containing the inventory resources')
    parser.add_argument('--with-meta', action='store_true', help='Include metadata in the inventory')
    parser.add_argument('--with-extra', action='store_true', help='Include Availability Zones, Regions and Account Attributes in the inventory')
    parser.add_argument('--with-empty', action='store_true', help='Include empty values in the inventory')
    args = parser.parse_args()

    resource_dir = args.resource_dir
    with_meta = args.with_meta
    with_extra = args.with_extra
    with_empty = args.with_empty

    # --- Find all policy files matching the pattern inventory_policy_local_X.json

    policy_files = glob.glob(os.path.join(resource_dir, 'inventory_policy_local_*.json'))

    # --- Find all YAML policy files in the policy directory

    yaml_policy_files = glob.glob(os.path.join(resource_dir, '*.yaml'))

    # --- Convert YAML policy files to JSON and keep this structure for later use

    inventory_structure = []
    for yaml_file in yaml_policy_files:
        with open(yaml_file, 'r') as file:
            yaml_content = yaml.safe_load(file)
            inventory_structure.append(yaml_content)

    if not inventory_structure:
        print("No resource files found.")
        sys.exit(1)

    # --- Perform inventory and list used resources

    resources_data = list_used_resources(inventory_structure)

    # That's all folks!