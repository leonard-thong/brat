#!/bin/sh

# Install script for brat server
#
# Author:   Sampo Pyysalo       <smp is s u tokyo ac jp>
# Author:   Pontus Stenetorp    <pontus is s u tokyo ac jp>
# Author:   Mengyang Liu        <myl is s of GT  us>
# Version:  2021-01-04

# defaults

WORK_DIR=work
DATA_DIR=data
CONFIG_TEMPLATE=config_template.py
CONFIG=config.py

# Absolute data and work paths

base_dir=`(cd \`dirname $0\`; pwd)`
SCRIPT_NAME=`basename $0`

work_dir_abs="$base_dir/$WORK_DIR"
data_dir_abs="$base_dir/$DATA_DIR"

USAGE="Usage: ${SCRIPT_NAME} [-u] [-q]"

# Options
UNPRIVILEGED=false
QUICK=false

while getopts uq OPT; do
    case "${OPT}" in
        u)
            UNPRIVILEGED=true
            ;;
	q)
            QUICK=true
            UNPRIVILEGED=true
	    ;;
        \?)
            echo ${USAGE} 1>&2
            exit 1
            ;;
    esac
done
shift `expr $OPTIND - 1`

if [ "$QUICK" = true ]; then
    user_name='default'
    password=`python3 -c 'import string; import random; print("".join(random.choice(string.ascii_letters+string.digits) for _ in range(10)))'`
    echo "Quick install: set user name to \"$user_name\" and password to \"$password\""
    admin_email='unconfigured@example.com'
else
    # not quick; ask details for config
    while true; do
	echo 'Please enter the user name that you want to use when logging into brat:'
	user_name="admin"
	if [ -n "$user_name" ]; then
	    break
	fi
    done
    while true; do
	echo "Please enter a brat password (this shows on screen):"
	password="1"
	if [ -n "$password" ]; then
	    break
	fi
    done
    echo "Please enter the administrator contact email:"
    admin_email="admin@dlwlrat.org"
fi

# Put a configuration in place.

(echo "# This configuration was automatically generated by ${SCRIPT_NAME}"
cat "$base_dir/$CONFIG_TEMPLATE" | \
    awk 'NR==1 { print "from os.path import dirname, join as path_join" } $1' | \
    sed \
    -e 's|\(ADMIN_CONTACT_EMAIL = \).*|\1'\'$admin_email\''|' \
    -e "s|\(BASE_DIR = \).*|\1dirname(__file__)|" \
    -e "s|\(DATA_DIR = \).*|\1path_join(BASE_DIR, '${DATA_DIR}')|" \
    -e "s|\(WORK_DIR = \).*|\1path_join(BASE_DIR, '${WORK_DIR}')|" \
    -e '/\(USER_PASSWORD *= *{.*\)/a\
        \    '\'"$user_name"\'': '\'"$password"\'',') > "$base_dir/$CONFIG"

# Create data and word directories and set up data (unless they exist already)

if [ -d $work_dir_abs ] && [ -d $data_dir_abs ]; then
    echo "Data and work directories $DATA_DIR/ and $WORK_DIR/ exist, skipping setup."
else
    mkdir -p $work_dir_abs
    mkdir -p $data_dir_abs

    # Try to determine Apache user and group

    apache_user=`ps aux | grep -v 'tomcat' | grep '[a]pache\|[h]ttpd' | cut -d ' ' -f 1 | grep -v '^root$' | head -n 1`
    apache_group=`groups $apache_user | head -n 1 | sed 's/ .*//'`

    # Place example data

    cp -r ${base_dir}/example-data/corpora ${DATA_DIR}/examples
    cp -r ${base_dir}/example-data/tutorials ${DATA_DIR}/tutorials

    # Place example configs

    cp ${base_dir}/configurations/example-conf/*.conf ${DATA_DIR}

    # Make $work_dir_abs and $data_dir_abs writable by Apache

    group_ok=0
    if [ "${UNPRIVILEGED}" != 'true' -a -n "$apache_group" -a -n "$apache_user" ] ; then
	echo "Assigning owner of the following directories to apache ($apache_group):"
	echo "    \"$work_dir_abs/\""
	echo "and"
	echo "    \"$data_dir_abs/\""
	echo "(this requires sudo; please enter your password if prompted)"

	sudo chgrp -R $apache_group $data_dir_abs $work_dir_abs
	RETVAL=$?
	if [ $RETVAL -eq 0 ]; then
	    chmod -R g+rwx $data_dir_abs $work_dir_abs
	    group_ok=1
	else
	    echo "WARNING: failed to change group."
	fi
    else
	if [ "${UNPRIVILEGED}" != 'true' ]; then
	    echo "WARNING: failed to determine Apache group."
	fi
    fi

    if [ $group_ok -eq 0 ]; then
	echo
	echo "Setting global read and write permissions to directories"
	echo "    \"$work_dir_abs/\" and"
	echo "    \"$data_dir_abs/\""
	echo "(you may wish to consider fixing this manually)"
	chmod -R 777 $data_dir_abs $work_dir_abs
    fi
fi

# Extract the most important library dependencies.

# ( cd server/lib && tar xfz simplejson-2.1.5.tar.gz )

# Dump some last instructions to the user

if [ "$QUICK" = true ]; then
    cat << EOF
Quick install: finished. To test brat, run the standalone server as

    python standalone.py

Please consider re-installing without "-q" for extended use (recommended).
EOF
else
    cat << EOF
The installation has finished, you are almost done.

1.) If you are installing brat on a webserver, make sure you have 
    followed the steps described in the brat manual to enable CGI:

    http://brat.nlplab.org/installation.html

2.) Please verify that brat is running by accessing your installation
    using a web browser.

You can automatically diagnose some common installation issues using:

    tools/troubleshooting.sh URL_TO_BRAT_INSTALLATION

If there are issues not detected by the above script, please contact the
brat developers and/or file a bug to the brat bug tracker:

    https://github.com/nlplab/brat/issues'

3.) Once brat is running, put your data in the data directory. Or use
    the example data placed there by the installation:

    ${data_dir_abs}

4.) You can find configuration files to place in your data directory in
    the configurations directory, see the manual for further details:

    ${base_dir}/configurations

5.) Then, you (and your team?) are ready to start annotating!
EOF
fi
