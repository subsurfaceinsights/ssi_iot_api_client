from ssi.iot_api_client import IotApiClient
from ssi.api_client import ApiException
import pandas as pd
import datetime
import os
from tabulate import tabulate
from pprint import pprint
from io import StringIO

# Some nice helper functions
def pretty_dict(d):
    print(tabulate(d.items()))

def pretty_list(d):
    if len(d) == 0 or not isinstance(d[0], dict):
        pprint(d)
    else:
        print(tabulate(d, headers='keys'))
# This token must be generated from the web interface.
# You can set it in your environemnt like this example or you can put it in
# your code. Be careful not to distribute code with secrets
token = os.environ.get('SSI_API_TOKEN')
client = IotApiClient(token = token)
# For debugging purposes, enable tracing
# client.api.trace = True

print("Connected, getting  devices")
# Grab a list of all the devices we can admin
devices = client.get_my_devices()
device = None
for d in devices:
    if d.hostname == "hanover-rev-g-greenhouse-system":
        device = d
        break
if not device:
    print("Could not find device")
    exit(1)


# Read the ert status of the system
ert_status = device.get_status('ert-status')
print("ERT Status")
pretty_dict(ert_status)
print('')

# List the user sequences, these are modifiable by the user
sequences = device.ls('config/sequences/user')
print("Sequences on system:")
pretty_list(sequences)
print('')

# Let's see what's in the first one. Pick another index to see others
sequence_file_name = sequences[0]['name']

sequence = device.get_file_data(f'config/sequences/user/{sequence_file_name}')
# We can read the sequence file as a pandas dataframe because it's just a tab
# separated values file
print(f"Contents of sequence file {sequence_file_name}")
df = pd.read_csv(StringIO(sequence), sep='\t')
print(df)

# Let's create a new sequence file
new_sequence_file_name = 'new_sequence.seq'
with open(new_sequence_file_name, 'w') as fp:
    # First we write the header. This is not used by the system but to help
    # humans, so the ordering of the seqeuence file is enforced and not
    # dependent on this header in the current iteration
    date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fp.write(f"# Sequence file example generated {date}\n")
    fp.write('Awell\tAelec\tBwell\tBelec\tMwell\tMelec\tNwell\tNelec\n')
    # Here is a hardcoded sequence as a list of tuples, but you could
    # programmatically generate one or someting. In this example, there are
    # only two

    # You could also use the pandas dataframe to generate and write the
    # sequence file. This is simpler for an example.
    sequence = [(1, 1, 1, 10, 1, 4, 1, 17),
                (1, 1, 1, 10, 1, 4, 1, 17)]
    for s in sequence:
        fp.write("\t".join([str(x) for x in s]) + "\n")

# The rest of the example requires that the device is connected
if device.connected == False:
    print("Device is not connected")
    exit(1)

# Get an event monitor for the device to monitor file transfers. We do this
# before uploading to ensure that we get the sync event, events are only
# monitored as long as the event monitor is alive, so if we were to upload and
# then get the event monitor, we could miss the event in a race condition if
# the upload syncs before we get the event monitor
event_monitor = device.gen_events()

print("Uploading new sequence file")
# Upload the new sequence file
sequence_file_path = f'config/sequences/user/{new_sequence_file_name}'

device.put_file(new_sequence_file_name,
                sequence_file_path, overwrite=True)
print("Done uploading, waiting to sync to device.")
print("Note that this event is only issued if the device is connected and the "
      "file actually changed. That's why we put the timestamp in the "
      "generated file")
print("This can take a while depending on the strength of the connection")
# Wait for the sync event
for event in event_monitor:
    print(event)
    if event['event'] == 'sync_to':
        if event['msg'].endswith(sequence_file_path):
            break
print("Synced to device")

# Now there are two ways to run the sequence, we can either run it by setting a
# the ert_acquisition configuration to have it collect the sequence as part of
# a scheduled survey, or we can run it manually. In this example we will run it
# manually using the device api directly

# This opens a handle to the device API, that is, API calls that are issued
# directly to the device software
dev_api = device.open_api()

# This is a list of calls.
pretty_list(dev_api.get_calls())
# If we are surveying, stop. This call succeeds even if we are not surveying
dev_api.call("stop_periodic_surveying")
# Show current ERT parameters. This shoudl match the ert-status call above
params = dev_api.call("get_ert_params")
pretty_dict(params)

sequences = dev_api.call("find_sequences")
pretty_list(sequences)

# We should have the sequence we uploaded, let's check. Note that the sequences
# reutrned here do not have an extension
sequence_file = '.'.join(new_sequence_file_name.split('.')[:-1])
assert sequence_file in sequences

# Now we can collect the sequence. We collect sequences as part of named
# surveys which are just a list a collection of acquisitions, either manually
# or periodically. If there is a problem with the start call, an exception is
# raised directly from the device with DeviceApiException type

survey_name = 'test_survey'

# Consult the manual for the available parameters for setting up an acquisition
dev_api.call("start_resistivity_acquisition",
    sequence_file=sequence_file,
    survey_name=survey_name,
    verbose_reporting=True, # This shows the electrode measurements as they
             # are collected
)

# We can monitor progress by getting the acquisition log. This is an event api
# endpoint, which means that the call is a generator that yields log messages as
# they are generated. This is a blocking call, so it will not return until the
# acquisition is complete.

for log in dev_api.event("get_acquisition_log"):
    print(log, end='')

# Acquisition file names are based on the survey name and the time the
# acquisition was started. We can get the file name by listing the acquisitions
# in the survey

acquisitions = dev_api.call("get_survey_acquisitions",
                            survey_name=survey_name
                           )
pretty_list(acquisitions)

# The latest acquisition should be the last one in the list
acquisition_name = acquisitions[-1]

# To save bandwidth, acquisitions are not immediately uploaded. We can mark
# them for upload here. ALternatively, we can also pull data directly from the
# device, but it is recommended to use the upload mechanism

dev_api.call("mark_acquisition_to_upload",
             survey_name=survey_name,
             acquisition_name=acquisition_name)

# Now we wait for the acquisition to upload, then we can download it
print("Waiting for device data to sync. This can take a bit")
for event in event_monitor:
    print(event)
    if event['event'] == 'sync_from':
        if event['msg'].endswith(f"{acquisition_name}.csv"):
            break
print("Synced from device")
print("Downloading acquisition data")
# Download the acquisition data. Acquisition data is always under the surveys
# 'ert' directory to distinguish it from other types of sensor data in a survey
survey_files = device.ls(f'data/{survey_name}/ert')
print("Here are the existing files in the survey directory")
pretty_list(survey_files)
# Download the acquisition data

acquisition_file = f"{acquisition_name}.csv"
device.get_file(f'data/{survey_name}/ert/{acquisition_file}', acquisition_file)
print("Done downloading acquisition data")
# Now we can load the data into a pandas dataframe and plot it, but only after
# stripping the metadata
clean_acquisition_file = f"{acquisition_name}_no_metadata.csv"
with open(acquisition_file) as fp, open(clean_acquisition_file, 'w') as fp_out:
    lines = fp.readlines()
    found_header = False
    for line in lines:
        if line.startswith('timestamp'):
            # We found the header
            found_header = True
        if found_header:
            fp_out.write(line)
df = pd.read_csv(clean_acquisition_file)
print(df.head())
print("Done")

# Here we could also do some analysis on the data, such as inversions.
# Subsurface Insights also offers a compute API service for running processing
# workflows with prepackaged modeling software, such as e4d, pygimli, and
# others

# Optionally, we can clean up files we don't need anymore.
# This isn't necessary and in fact if you are collecting interesting data, you
# may want to keep it around. Since this is a one-off example, we will clean up
# the files we created

# Discard the acquisition data on the IOT server
device.rm(f'config/sequences/user/{new_sequence_file_name}')
device.rm(f'data/{survey_name}/ert/{acquisition_file}')
# Discard the acquisition data on the device
dev_api.call('discard_acquisition', survey_name=survey_name,
             acquisition_name=acquisition_name)
# Remove local files
os.remove(new_sequence_file_name)
os.remove(acquisition_file)
os.remove(clean_acquisition_file)
# We can close the device api handle
dev_api.close()
# Finally, we can close the event monitor
event_monitor.close()
