'''
    This program uses Simpy to simulate the OpenUp Counselling Service 
    platform and helpseeker arrivals.  

    Helpseekers will renege when they loose patience waiting in the queue

    For more details about the Simpy syntax, please visit
    https://simpy.readthedocs.io/en/latest/contents.html
'''

import simpy, random
# from scipy.stats import poisson




# Globals
NUM_HELPSEEKERS_IN_SYSTEM = 0
NUM_HELPSEEKERS_IN_QUEUE = 0
NUM_HELPSEEKERS_IN_PRIORITY_QUEUE = [0, 0, 0]
MEAN_QUEUE_TIME_BEFORE_RENEGING = 7.0 # specify this as a float
LAMBDA_RENEGE = 1.0/MEAN_QUEUE_TIME_BEFORE_RENEGING # mean reneging counts
LUNCH_BREAK = 60 # 60 minute lunch break
MEAN_CHAT_DURATION = 60 # average chat no longer than 60 minutes
SIMULATION_DURATION = 1440
MEAN_INTERARRIVAL_TIME = 7.0 # mean time between helpseekers arriving
NUM_COUNSELLORS = 16
SEED = 728

RISKLEVEL_WEIGHTS = {
    'CRISIS':   .05,
    'HIGH':     .015,
    'MEDIUM':   .16,
    'LOW':      .82,
} # Distribution of LOW/MEDIUM/HIGH/CRISIS - 82%/16%/1.5%/0.5%

USERTYPE_WEIGHTS = {
    'REPEATED_USER': , .08
    'REGULAR_USER': , .92
}


################################################################################
# Drawing arrival times from distribitions
################################################################################

def assign_interarrival_time(mean_interarrival_time):
    '''
        Getter to assign interarrival time

        param:
            mean_interarrival_time - mean interarrival time between helpseekers
    '''
    lambda_interarrival_time = 1.0/mean_interarrival_time
    return random.expovariate(lambda_interarrival_time)

#-------------------------------------------------------------------------------

def assign_chat_duration(mean_chat_duration):
    '''
        Getter to assign chat duration

        param:
            mean_chat_duration - mean chat duration of a conversation
    '''
    chat_duration_lambda = 1.0 / mean_chat_duration
    return random.expovariate(chat_duration_lambda)

#-------------------------------------------------------------------------------

def assign_priority():
    '''
        Getter to assign helpseeker priority
    '''

    return random.choices(
        list(RISKLEVEL_WEIGHTS.keys()), list(RISKLEVEL_WEIGHTS.values() ) )[0]

#-------------------------------------------------------------------------------

def assign_repeated_user():
    '''
        Getter to assign repeated user status
    '''

    return random.choices(
        list(USERTYPE_WEIGHTS.keys()), list(USERTYPE_WEIGHTS.values() ) )[0]

################################################################################
# Classes
################################################################################

class Counsellor:
    '''
        Counsellor Class with a limited number of counsellors to serve helpseekers
    '''

    def __init__(self, 
                 env,
                 counsellor_id,
                 service_duration,
                 lunch_duration=LUNCH_BREAK):
        self.env = env
        self.process = env.process(self.shift() )
        self.counsellor = f'Counsellor {counsellor_id}'

        self.service_duration = service_duration
        self.lunch_duration = lunch_duration

    #---------------------------------------------------------------------------
    
    def shift(self):
        '''
            Define a shift
        '''

        while True:
            # start counselling for the first 6 hours
            print(f'{self.counsellor} starts shift at {self.env.now}')
            yield self.env.timeout(self.service_duration)

            # give lunch break, but throw an interrupt when the queue grows too
            # long
            try:
                yield self.env.process(self.lunch_break() )
            except simpy.Interrupt:

            

            yield self.env.timeout(self.service_duration)
            print(f'{self.counsellor} shift ends at {self.env.now}')

    
    #---------------------------------------------------------------------------

    def lunch_break(self):
        '''
            Give counsellors a lunch break
            but interrupt when there is an urgent case
        '''

        print(f'{self.counsellor} takes a lunch break at {self.env.now}')
        yield self.env.timeout(self.lunch_duration)
        print(f'{self.counsellor} lunch break ends at {self.env.now}')

#-------------------------------------------------------end of Counsellors class

class Helpseeker:
    '''
        Helpseeker Class to create a helpseeker
    '''

    def __init__(self,
                 env,
                 helpseeker_id,
                 openup_service,
                 mean_chat_duration=MEAN_CHAT_DURATION,
                 mean_interarrival_time=MEAN_INTERARRIVAL_TIME):

        '''
            param:
                env - simpy environment instance
                helpseeker_id - an assigned helpseeker id
                openup_service - Openup counselling service resource instance
                mean_chat_duration - mean chat duration
                mean_interarrival_time - mean interarrival time
        '''

        self.env = env
        self.arrival_time = None
        self.openup_service = openup_service
        self.user = f'Helpseeker {helpseeker_id}'
        self.mean_chat_duration = mean_chat_duration
        self.mean_interarrival_time = mean_interarrival_time

        # start creating a chatroom session
        self.process = env.process(self.session() )

    #---------------------------------------------------------------------------

    def session(self):
        '''
            Function to create a chatroom session
        '''

        # simulate the interarrivals between users
        interarrival_time = assign_interarrival_time(self.mean_interarrival_time)
        yield self.env.timeout(interarrival_time)

        self.arrival_time = self.env.now
        print(f'{self.user} has accepted TOS.  '
            f'Chat session created at t = {self.env.now}.')


        with self.openup_service.request() as req:
            # helpseeker patience follows an exponential distribution
            # wait for counsellor or renege
            patience = random.expovariate(LAMBDA_RENEGE)
            results = yield req | self.env.timeout(patience)
            
            if req in results:
                # counsellor now serving helpseeker
                time_now = self.env.now
                queue_time = time_now - self.arrival_time
                print(f'{self.user} now being served at {time_now}.  '
                    f'User spent {queue_time} in the queue.')

                chat_duration = assign_chat_duration(self.mean_chat_duration)
                yield self.env.timeout(chat_duration)
                print(f'{self.user} chat session terminated successfully at t ='
                    f' {self.env.now}. Chat lasted {chat_duration}')

            else:
                # helpseeker reneged
                print(f'{self.user} reneged after '
                    f'spending t = {patience} in the queue.')

################################################################################
# Main Function
################################################################################

def main():
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~')
    print('Initializing OpenUp Queue Simulation')
    print('~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~\n')
    random.seed(SEED) # comment out line if not reproducing results

    # create environment
    env = simpy.Environment()
    # counsellor = Counsellor(env, 1, 8, 1)
    
    counsellor = simpy.Resource(env, capacity=NUM_COUNSELLORS)

    helpseekers = []
    start_time = env.now
    duration = 0

    for i in range(1, 120):
        helpseekers.append(Helpseeker(env, i, counsellor) )

    print(f'Total number of helpseekers created: {len(helpseekers)}\n\n')

    env.run(until=SIMULATION_DURATION) # daily simulation at the queue


if __name__ == '__main__':
    main()






# class QueueSimulationModel:
#     '''
#         The Queue Simulation Model
#     '''

#     def __init(self):
#         self.env = sp.Environment() # the execution environment
