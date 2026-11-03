"""CPU nodes for reproducible Bratos satellite simulations.

The default two Ubuntu hosts support the original independent provider jobs or
the Option 2 coupled geographic workers. No GPU or shared filesystem is required.
The Bratos CloudLab harness transfers a frozen simulator and orbital catalog
after provisioning. The Option 2 adapter checkpoints each epoch on its coordinator;
the original adapter collects completed seed checkpoints on the laptop.

Instructions:
Wait until the experiment is ready and the startup services have completed.
Copy the node SSH addresses from List View into cloudlab/.env in your local
Bratos checkout, setting CLOUDLAB_REMOTE_DIR=/users/YOUR_USERNAME/bratos-cloudlab.
Run `make -C cloudlab setup`, `make -C cloudlab smoke`, and
`make -C cloudlab wait`. Calibrate before starting the full 1,200 five-second
epochs (100 simulated minutes), and set the worker time limit to fit the
experiment lifetime. Fetch results before expiration; experiment disks are
ephemeral. The profile installs only Ubuntu Python/venv and rsync prerequisites;
the harness installs the pinned simulation dependencies in a virtual environment.
"""

import geni.portal as portal
import geni.rspec.pg as pg

context = portal.context
request = context.makeRequestRSpec()
context.defineParameter("nodeCount", "Number of CPU nodes", portal.ParameterType.INTEGER, 2)
context.defineParameter(
    "hardwareType", "Node type (blank lets CloudLab select)",
    portal.ParameterType.STRING, "",
)
parameters = context.bindParameters()
if not 1 <= parameters.nodeCount <= 40:
    context.reportError(portal.ParameterError("Choose between 1 and 40 nodes.", ["nodeCount"]))
context.verifyParameters()

for index in range(parameters.nodeCount):
    node = request.RawPC("node%d" % index)
    node.disk_image = "urn:publicid:IDN+emulab.net+image+emulab-ops//UBUNTU22-64-STD"
    if parameters.hardwareType.strip():
        node.hardware_type = parameters.hardwareType.strip()
    node.addService(pg.Execute(
        shell="bash",
        command="sudo apt-get update && sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv rsync",
    ))

context.printRequestRSpec()
