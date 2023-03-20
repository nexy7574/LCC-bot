# LCC Bot Docker Setup

Setting up good ol' Jimmy in a docker container isn't as straight forward as build & run.
In order to get the bot to work, you need to do a few things:

1. Create a .env in the root directory of the project
2. Look at [config.example.py](/config.example.py) and copy the values you want into the .env file.
3. Build the docker image with `docker build -t lcc-bot:latest .`
4. Run the docker image with `docker run -d --name lcc-bot lcc-bot:latest`

## Configuration
Unlike the non-docker setup, you need to configure the bot using environment variables.
The environment variables are the same as the ones in [config.example.py](/config.example.py), however more limited.

All values are expected to be strings and are parsed appropriately.
Take a look at [config_docker.py](/config_docker.py) to see how values are parsed, and what the defaults are,
as this is the file that is copied over.

> WARNING: REMEMBER, YOU DON'T USE config.py FOR DOCKER!

## Where does the database go?
If Jimmy detects a `/data` directory, it will use that as the database location. This means you can do a bind mount to the host filesystem when
running `docker run`, using the argument `-v /path/to/host/dir:/data`.

## Exposing the API
Jimmy by default runs a tiny API on port 3762. In order to make this accessible, you will need to pass `-p <host_port>:3762` to `docker run`.

## Example run command
```shell
$ docker build -t lcc-bot:latest .
...

$ docker run -d --name lcc-bot -v /path/to/host/dir:/data -p 3762:3762 lcc-bot:latest
```
